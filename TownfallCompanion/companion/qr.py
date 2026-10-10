"""QR codes for the companion's window: the phone's address as a picture the phone's camera reads.

Written from the QR code specification (ISO/IEC 18004) for what the window needs and no more: text in byte
mode, error correction level M (a smudge or a reflection on the screen can hide 15% of it), versions 1 to 6.
Version 6 holds 106 bytes; http://255.255.255.255:65535/check is 34.
"""

# Level M, per version: (error correction codewords per block, blocks, data codewords per block).
BLOCKS = {1: (10, 1, 16), 2: (16, 1, 28), 3: (26, 1, 44), 4: (18, 2, 32), 5: (24, 2, 43), 6: (16, 4, 27)}
LEVEL_M = 0b00      # the level's two bits in the format information
BYTE_MODE = 0b0100
PAD_BYTES = (0xEC, 0x11)
FORMAT_GENERATOR = 0b10100110111  # the BCH (15, 5) code's generator polynomial
FORMAT_XOR = 0b101010000010010    # keeps the format information from ever being all light

# The eight mask patterns by (row, column): the modules where one is true are flipped.
MASKS = (
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: r * c % 2 + r * c % 3 == 0,
    lambda r, c: (r * c % 2 + r * c % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + r * c % 3) % 2 == 0,
)

# Arithmetic in GF(256) as QR codes do it, modulo x^8 + x^4 + x^3 + x^2 + 1: EXP[i] is 2 to the power i.
EXP = [0] * 512
LOG = [0] * 256
_value = 1
for _power in range(255):
    EXP[_power] = _value
    LOG[_value] = _power
    _value <<= 1
    if _value & 0x100:
        _value ^= 0x11D
for _power in range(255, 512):
    EXP[_power] = EXP[_power - 255]  # so a sum of two logarithms needs no modulo


def multiply(a, b):
    return 0 if a == 0 or b == 0 else EXP[LOG[a] + LOG[b]]


def size(version):
    return 17 + 4 * version


def capacity(version):
    """How many bytes of text the version holds at level M: its data codewords, less the 4-bit mode and the
    8-bit length."""
    _, blocks, data = BLOCKS[version]
    return (blocks * data * 8 - 12) // 8


def version_for(length):
    for version in BLOCKS:
        if length <= capacity(version):
            return version
    raise ValueError(f"{length} bytes don't fit a version 1 to 6 QR code (at most {capacity(6)})")


def error_correction(data, count):
    """The Reed-Solomon error correction codewords for data: the remainder of data * x^count divided by the
    generator polynomial, the product of (x - 2^i) for i below count."""
    generator = [1]
    for i in range(count):
        # times (x + 2^i): subtraction is addition in GF(256)
        generator = [a ^ multiply(b, EXP[i]) for a, b in zip(generator + [0], [0] + generator)]
    remainder = [0] * count
    for byte in data:
        factor = byte ^ remainder[0]
        remainder = remainder[1:] + [0]
        # generator[0] is 1, the leading term that the subtraction cancels
        remainder = [r ^ multiply(g, factor) for r, g in zip(remainder, generator[1:])]
    return remainder


def codewords(data, version):
    """The data with its mode and length, padded to the version's size, split into blocks, each followed by its
    error correction, and interleaved: what is drawn into the symbol, in order."""
    ec_count, blocks, per_block = BLOCKS[version]
    bits = f"{BYTE_MODE:04b}{len(data):08b}" + "".join(f"{byte:08b}" for byte in data)
    room = blocks * per_block * 8
    bits += "0" * min(4, room - len(bits))  # the terminator, as much of it as fits
    bits += "0" * (-len(bits) % 8)
    stream = [int(bits[i:i + 8], 2) for i in range(0, len(bits), 8)]
    stream += [PAD_BYTES[i % 2] for i in range(blocks * per_block - len(stream))]
    split = [stream[i * per_block:(i + 1) * per_block] for i in range(blocks)]
    corrections = [error_correction(block, ec_count) for block in split]
    # All blocks are the same length at level M up to version 6, so interleaving is a plain zip.
    return [byte for column in zip(*split) for byte in column] + \
           [byte for column in zip(*corrections) for byte in column]


def format_bits(mask):
    """The 15 bits that say the level and the mask: 5 data bits, 10 BCH error correction bits, XOR'ed."""
    data = LEVEL_M << 3 | mask
    remainder = data << 10
    for shift in range(4, -1, -1):
        if remainder & (1 << (shift + 10)):
            remainder ^= FORMAT_GENERATOR << shift
    return (data << 10 | remainder) ^ FORMAT_XOR


def format_positions(version):
    """The two places (row, column) of the format information's 15 bits, most significant first: around the
    top-left finder, and split between the other two."""
    n = size(version)
    around = [(8, c) for c in (0, 1, 2, 3, 4, 5, 7, 8)] + [(r, 8) for r in (7, 5, 4, 3, 2, 1, 0)]
    split = [(n - 1 - i, 8) for i in range(7)] + [(8, n - 8 + i) for i in range(8)]
    return around, split


def alignment_centres(version):
    # Up to version 6 there is one alignment pattern, seven modules in from the bottom-right corner.
    return [] if version == 1 else [(size(version) - 7, size(version) - 7)]


def function_patterns(version):
    """The symbol's fixed parts: (dark, reserved), both grids of rows. Reserved holds every module that isn't
    data: finders with their light separators, timing lines, the alignment pattern, the format information's
    places (light until written) and the one module that is always dark."""
    n = size(version)
    dark = [[False] * n for _ in range(n)]
    reserved = [[False] * n for _ in range(n)]

    def put(r, c, value):
        if 0 <= r < n and 0 <= c < n:
            dark[r][c], reserved[r][c] = value, True

    for top, left in ((0, 0), (0, n - 7), (n - 7, 0)):
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                ring = max(abs(dr - 3), abs(dc - 3))  # 0 to 1: the centre, 2: light, 3: the frame, 4: separator
                put(top + dr, left + dc, ring <= 1 or ring == 3)
    for i in range(8, n - 8):
        put(6, i, i % 2 == 0)
        put(i, 6, i % 2 == 0)
    for row, col in alignment_centres(version):
        for dr in range(-2, 3):
            for dc in range(-2, 3):
                put(row + dr, col + dc, max(abs(dr), abs(dc)) != 1)
    for position in sum(format_positions(version), []):
        put(*position, False)
    put(n - 8, 8, True)
    return dark, reserved


def data_positions(reserved):
    """The data modules in drawing order: two columns at a time from the right, up and down in turn, skipping the
    vertical timing line; in each row the right module first."""
    n = len(reserved)
    right, upward = n - 1, True
    while right > 0:
        if right == 6:
            right = 5
        for row in (range(n - 1, -1, -1) if upward else range(n)):
            for col in (right, right - 1):
                if not reserved[row][col]:
                    yield row, col
        right -= 2
        upward = not upward


def penalty(grid):
    """How hard the symbol is to read, by the specification's four rules: long runs of one colour, 2x2 blocks,
    lookalikes of the finder pattern, and a dark share far from half."""
    n = len(grid)
    lines = ["".join("1" if module else "0" for module in row) for row in grid]
    lines += ["".join(lines[r][c] for r in range(n)) for c in range(n)]
    score = 0
    for line in lines:
        run = 1
        for module, following in zip(line, line[1:] + "x"):
            if following == module:
                run += 1
            else:
                if run >= 5:
                    score += run - 2
                run = 1
        padded = "0000" + line + "0000"  # the quiet zone around the symbol is light
        for pattern in ("00001011101", "10111010000"):
            score += 40 * sum(padded.startswith(pattern, i) for i in range(len(padded) - 10))
    for r in range(n - 1):
        for c in range(n - 1):
            if grid[r][c] == grid[r][c + 1] == grid[r + 1][c] == grid[r + 1][c + 1]:
                score += 3
    dark = sum(map(sum, grid))
    score += 10 * (abs(dark * 20 - n * n * 10) // (n * n))
    return score


def encode(text, mask=None):
    """The QR code of text (UTF-8) as rows of modules, True for dark, without the quiet zone (four light modules
    on every side) that has to surround it. The mask is the most readable one unless given."""
    data = text.encode("utf-8")
    version = version_for(len(data))
    fixed, reserved = function_patterns(version)
    bits = "".join(f"{byte:08b}" for byte in codewords(data, version))
    unmasked = [row[:] for row in fixed]
    places = list(data_positions(reserved))
    for (row, col), bit in zip(places, bits):
        unmasked[row][col] = bit == "1"  # modules past the last codeword (the remainder bits) stay light

    candidates = []
    for number in (range(8) if mask is None else (mask,)):
        grid = [row[:] for row in unmasked]
        flip = MASKS[number]
        for row, col in places:
            if flip(row, col):
                grid[row][col] = not grid[row][col]
        word = format_bits(number)
        for copy in format_positions(version):
            for i, (row, col) in enumerate(copy):
                grid[row][col] = bool(word >> (14 - i) & 1)
        candidates.append((penalty(grid) if mask is None else 0, number, grid))
    return min(candidates, key=lambda candidate: candidate[:2])[2]
