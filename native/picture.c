/* The CRTV screen's picture for the phone, included by tf_native.c before capture.c. The screen is cut out of the
 * texture the game draws it into, scaled to the width the phone wants and encoded as JPEG with Windows Imaging
 * Component (WIC, part of Windows), on a thread of its own: only the JPEG goes to shared memory, and the companion
 * (crtv_native.py) passes it on as it is. */
#define COBJMACROS
#include <wincodec.h>

/* Where the screen is in the 2048x2048 texture the game draws it into, measured in the game, whatever the screen
 * shows: a band across it, the rest isn't part of the screen. A texture of another size is taken whole. */
#define SCREEN_TEXTURE_SIZE 2048
static const int SCREEN_BOX[4] = {6, 345, 2044, 1728}; /* left, top, right, bottom (exclusive) */
#define PICTURE_MAX_WIDTH 640
#define PICTURE_MAX_BYTES (1u << 20)                    /* a 640-wide JPEG is about 50 KB */
#define PICTURE_QUALITY 0.82f
#define PICTURE_ROWS_BYTES ((PICTURE_MAX_WIDTH * 3 + 3) * 2048)

/* WIC's own identifiers (wincodec.h), defined here rather than pulled from a library. */
static const GUID WIC_FACTORY = {0xcacaf262, 0x9370, 0x4615, {0xa1, 0x3b, 0x9f, 0x55, 0x39, 0xda, 0x4c, 0x0a}};
static const GUID WIC_FACTORY_IID = {0xec5ec8a9, 0xc395, 0x4314, {0x9c, 0x77, 0x54, 0xd7, 0xa9, 0x35, 0xff, 0x70}};
static const GUID WIC_JPEG = {0x19e4a5aa, 0x5662, 0x4fc5, {0xa0, 0xc0, 0x17, 0x58, 0x02, 0x8e, 0x10, 0x57}};
static const GUID WIC_BGR24 = {0x6fddc324, 0x4e03, 0x4bfe, {0xb1, 0x85, 0x3d, 0x77, 0x76, 0x8d, 0xc9, 0x0c}};

/* What the companion reads: after the counter (odd while it is written), this header, then the JPEG. */
struct PictureHeader {
    char magic[8];                       /* "TFJPEG01" */
    uint32_t width, height, bytes, reserved;
    uint64_t sequence, unix_ms;
};
_Static_assert(sizeof(struct PictureHeader) == 40, "the companion's view of the picture (crtv_native.py)");
#define PICTURE_HEADER_AT 8u
#define PICTURE_AT 64u
#define PICTURE_MAPPING_SIZE (PICTURE_AT + PICTURE_MAX_BYTES)

static IWICImagingFactory *wic;
static unsigned char picture_rows[PICTURE_ROWS_BYTES]; /* the scaled screen, BGR (the picture thread's) */
static unsigned char picture_jpeg[PICTURE_MAX_BYTES];
static _Atomic unsigned requested_width = PICTURE_MAX_WIDTH;

/* The screen in `pixels` (width x height, 4 bytes a pixel, BGRA if `bgra`, else RGBA; the fourth byte unused),
 * scaled to `out_width` or its own width if that is less, each picture pixel the average of the texture's pixels
 * under it, as BGR rows `*stride` bytes apart in picture_rows. Returns the picture's height; *out_width becomes its
 * width. */
static int scale_screen(const unsigned char *pixels, int width, int height, int bgra, int *out_width, UINT *stride) {
    int box[4] = {0, 0, width, height};
    if (width == SCREEN_TEXTURE_SIZE && height == SCREEN_TEXTURE_SIZE) memcpy(box, SCREEN_BOX, sizeof(box));
    int box_width = box[2] - box[0], box_height = box[3] - box[1];
    int picture_width = *out_width < box_width ? *out_width : box_width;
    int picture_height = (box_height * picture_width + box_width / 2) / box_width;
    if (picture_height < 1) picture_height = 1;
    *out_width = picture_width;
    *stride = ((UINT)picture_width * 3 + 3) & ~3u;
    int red = bgra ? 2 : 0, blue = bgra ? 0 : 2;
    for (int y = 0; y < picture_height; ++y) {
        int top = box[1] + y * box_height / picture_height, bottom = box[1] + (y + 1) * box_height / picture_height;
        if (bottom <= top) bottom = top + 1;
        unsigned char *row = picture_rows + (size_t)y * *stride;
        for (int x = 0; x < picture_width; ++x) {
            int left = box[0] + x * box_width / picture_width, right = box[0] + (x + 1) * box_width / picture_width;
            if (right <= left) right = left + 1;
            unsigned sum[3] = {0, 0, 0}, count = (unsigned)((bottom - top) * (right - left));
            for (int sy = top; sy < bottom; ++sy) {
                const unsigned char *texel = pixels + ((size_t)sy * width + left) * 4;
                for (int sx = left; sx < right; ++sx, texel += 4) {
                    sum[0] += texel[blue];
                    sum[1] += texel[1];
                    sum[2] += texel[red];
                }
            }
            for (int c = 0; c < 3; ++c) row[x * 3 + c] = (unsigned char)((sum[c] + count / 2) / count);
        }
    }
    return picture_height;
}

/* The picture in picture_rows as JPEG into picture_jpeg: its length, 0 if WIC couldn't. */
static DWORD encode_picture(int width, int height, UINT stride) {
    if (!wic && FAILED(CoCreateInstance(&WIC_FACTORY, NULL, CLSCTX_INPROC_SERVER, &WIC_FACTORY_IID, (void **)&wic)))
        return 0;
    IWICStream *stream = NULL;
    IWICBitmapEncoder *encoder = NULL;
    IWICBitmapFrameEncode *frame = NULL;
    IPropertyBag2 *options = NULL;
    PROPBAG2 quality_option = {0};
    quality_option.pstrName = L"ImageQuality";
    VARIANT quality;
    memset(&quality, 0, sizeof(quality));
    V_VT(&quality) = VT_R4;
    V_R4(&quality) = PICTURE_QUALITY;
    WICPixelFormatGUID format = WIC_BGR24;
    LARGE_INTEGER here = {0};
    ULARGE_INTEGER end = {0};
    DWORD length = 0;
    if (SUCCEEDED(IWICImagingFactory_CreateStream(wic, &stream)) &&
        SUCCEEDED(IWICStream_InitializeFromMemory(stream, picture_jpeg, sizeof(picture_jpeg))) &&
        SUCCEEDED(IWICImagingFactory_CreateEncoder(wic, &WIC_JPEG, NULL, &encoder)) &&
        SUCCEEDED(IWICBitmapEncoder_Initialize(encoder, (IStream *)stream, WICBitmapEncoderNoCache)) &&
        SUCCEEDED(IWICBitmapEncoder_CreateNewFrame(encoder, &frame, &options)) &&
        SUCCEEDED(IPropertyBag2_Write(options, 1, &quality_option, &quality)) &&
        SUCCEEDED(IWICBitmapFrameEncode_Initialize(frame, options)) &&
        SUCCEEDED(IWICBitmapFrameEncode_SetSize(frame, (UINT)width, (UINT)height)) &&
        SUCCEEDED(IWICBitmapFrameEncode_SetPixelFormat(frame, &format)) && IsEqualGUID(&format, &WIC_BGR24) &&
        SUCCEEDED(IWICBitmapFrameEncode_WritePixels(frame, (UINT)height, stride, stride * (UINT)height,
                                                    picture_rows)) &&
        SUCCEEDED(IWICBitmapFrameEncode_Commit(frame)) && SUCCEEDED(IWICBitmapEncoder_Commit(encoder)) &&
        SUCCEEDED(IWICStream_Seek(stream, here, STREAM_SEEK_CUR, &end)))
        length = (DWORD)end.QuadPart;
    if (options) IPropertyBag2_Release(options);
    if (frame) IWICBitmapFrameEncode_Release(frame);
    if (encoder) IWICBitmapEncoder_Release(encoder);
    if (stream) IWICStream_Release(stream);
    return length;
}

/* The picture of `pixels` (the texture captured, see scale_screen) to shared memory at `shared`, as picture
 * `sequence`. 1 once there. */
static int publish_picture(unsigned char *shared, const unsigned char *pixels, int width, int height, int bgra,
                           uint64_t sequence) {
    int picture_width = (int)atomic_load(&requested_width);
    UINT stride = 0;
    int picture_height = scale_screen(pixels, width, height, bgra, &picture_width, &stride);
    DWORD bytes = encode_picture(picture_width, picture_height, stride);
    if (!shared || !bytes) return 0;
    struct PictureHeader header = {{'T', 'F', 'J', 'P', 'E', 'G', '0', '1'}, (uint32_t)picture_width,
                                   (uint32_t)picture_height, bytes, 0, sequence, unix_ms()};
    InterlockedIncrement((volatile LONG *)shared); /* odd: being written */
    memcpy(shared + PICTURE_HEADER_AT, &header, sizeof(header));
    memcpy(shared + PICTURE_AT, picture_jpeg, bytes);
    MemoryBarrier();
    InterlockedIncrement((volatile LONG *)shared); /* even: whole */
    return 1;
}
