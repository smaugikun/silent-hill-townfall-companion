"""The QR encoder of the companion's window (companion/qr.py), against the QR code specification's own numbers."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "TownfallCompanion" / "companion"))
import qr  # noqa: E402

# The specification's format information for level M and masks 0 to 7, after the XOR.
FORMAT_M = ["101010000010010", "101000100100101", "101111001111100", "101101101001011",
            "100010111111001", "100000011001110", "100111110010111", "100101010100000"]
# Codewords per version, data and error correction together (the specification's table of capacities).
TOTAL_CODEWORDS = {1: 26, 2: 44, 3: 70, 4: 100, 5: 134, 6: 172}
FINDER = ["#######", "#.....#", "#.###.#", "#.###.#", "#.###.#", "#.....#", "#######"]
# http://192.168.1.50:8790 as this encoder draws it (version 2, mask 2). An independent QR reader reads it back as
# that text; the test keeps it from changing unnoticed.
KNOWN = [
    "#######..#.##.#...#######",
    "#.....#.......##..#.....#",
    "#.###.#.#..##.#.#.#.###.#",
    "#.###.#.#.#.##....#.###.#",
    "#.###.#.#.....#...#.###.#",
    "#.....#.##.##..##.#.....#",
    "#######.#.#.#.#.#.#######",
    "........#.#.#..#.........",
    "#.#####..##.##.##.#####..",
    ".......#.##..##..#.....#.",
    ".##.#.###.####.#..##.#.##",
    ".#.#.#...###....#...#...#",
    ".#..#.###..#.#....###.###",
    "##..##..##..###......#.#.",
    "#..#..#...#....#.#.#.#.##",
    "#....#.#..#.#..#.....#..#",
    "#.#...#..##.##..#####.#..",
    "........##....#.#...###..",
    "#######..######.#.#.#####",
    "#.....#.##.#..###...##..#",
    "#.###.#.##.###.########..",
    "#.###.#.##..#####.###.###",
    "#.###.#.#...#....#....#.#",
    "#.....#...#.#.#.#.####..#",
    "#######.##...#.#.########",
]


def picture(grid):
    return ["".join("#" if module else "." for module in row) for row in grid]


def format_words(grid):
    """The two copies of the format information, read from their places in the symbol."""
    version = (len(grid) - 17) // 4
    return ["".join("1" if grid[r][c] else "0" for r, c in copy) for copy in qr.format_positions(version)]


class QrTest(unittest.TestCase):
    def test_the_symbol_grows_by_four_modules_per_version(self):
        for version in range(1, 7):
            grid = qr.encode("x" * qr.capacity(version))
            self.assertEqual(len(grid), 17 + 4 * version, version)
            self.assertEqual({len(row) for row in grid}, {len(grid)}, version)

    def test_level_m_capacities_and_the_smallest_version_that_holds_the_text(self):
        self.assertEqual([qr.capacity(v) for v in range(1, 7)], [14, 26, 42, 62, 84, 106])
        self.assertEqual([qr.version_for(n) for n in (1, 14, 15, 42, 43, 106)], [1, 1, 2, 3, 4, 6])
        self.assertEqual(qr.version_for(len("http://255.255.255.255:65535/check")), 3)
        with self.assertRaises(ValueError):
            qr.version_for(107)

    def test_every_codeword_has_its_place(self):
        for version, total in TOTAL_CODEWORDS.items():
            _, reserved = qr.function_patterns(version)
            remainder = 0 if version == 1 else 7  # the bits left over in versions 2 to 6
            self.assertEqual(len(list(qr.data_positions(reserved))), total * 8 + remainder, version)
            self.assertEqual(len(qr.codewords(b"x", version)), total, version)

    def test_finder_patterns_sit_in_three_corners_with_light_separators(self):
        grid = picture(qr.encode("http://192.168.1.50:8790/check"))
        n = len(grid)
        for top, left in ((0, 0), (0, n - 7), (n - 7, 0)):
            self.assertEqual([row[left:left + 7] for row in grid[top:top + 7]], FINDER, (top, left))
        self.assertEqual(grid[7][:8], "........")
        self.assertEqual(grid[7][n - 8:], "........")
        self.assertEqual(grid[n - 8][:8], "........")
        self.assertEqual("".join(row[7] for row in grid[:8]), "........")
        self.assertNotEqual([row[n - 7:] for row in grid[n - 7:]], FINDER)  # the fourth corner has none

    def test_timing_lines_alternate_and_the_dark_module_is_dark(self):
        grid = picture(qr.encode("http://10.0.0.2:8790"))
        n = len(grid)
        expected = "".join("#" if i % 2 == 0 else "." for i in range(8, n - 8))
        self.assertEqual(grid[6][8:n - 8], expected)
        self.assertEqual("".join(row[6] for row in grid[8:n - 8]), expected)
        self.assertEqual(grid[n - 8][8], "#")

    def test_the_alignment_pattern_from_version_2(self):
        grid = picture(qr.encode("http://192.168.178.23:8790"))
        n = len(grid)
        self.assertEqual([row[n - 9:n - 4] for row in grid[n - 9:n - 4]],
                         ["#####", "#...#", "#.#.#", "#...#", "#####"])
        self.assertEqual(qr.alignment_centres(1), [])

    def test_format_information_matches_the_specifications_table(self):
        self.assertEqual([f"{qr.format_bits(mask):015b}" for mask in range(8)], FORMAT_M)

    def test_both_copies_of_the_format_information_say_level_m_and_the_mask_used(self):
        for mask in range(8):
            first, second = format_words(qr.encode("http://192.168.1.50:8790", mask))
            self.assertEqual(first, second, mask)
            self.assertEqual(first, FORMAT_M[mask], mask)
        chosen = format_words(qr.encode("http://192.168.1.50:8790"))[0]
        self.assertIn(chosen, FORMAT_M)

    def test_error_correction_matches_published_examples(self):
        # "01234567" and "HELLO WORLD", both version 1-M: the data codewords and their error correction.
        numeric = [0x10, 0x20, 0x0C, 0x56, 0x61, 0x80, 0xEC, 0x11, 0xEC, 0x11, 0xEC, 0x11, 0xEC, 0x11, 0xEC, 0x11]
        self.assertEqual(qr.error_correction(numeric, 10), [0xA5, 0x24, 0xD4, 0xC1, 0xED, 0x36, 0xC7, 0x87, 0x2C, 0x55])
        hello = [32, 91, 11, 120, 209, 114, 220, 77, 67, 64, 236, 17, 236, 17, 236, 17]
        self.assertEqual(qr.error_correction(hello, 10), [196, 35, 39, 119, 235, 215, 231, 226, 93, 23])

    def test_the_data_is_byte_mode_with_its_length_and_padding(self):
        words = qr.codewords(b"AB", 1)
        self.assertEqual(words[:4], [0b01000000, 0b00100100, 0b00010100, 0b00100000])  # 0100, 2, "A", "B", 0000
        self.assertEqual(words[4:16], [0xEC, 0x11] * 6)

    def test_the_same_text_gives_the_same_symbol(self):
        self.assertEqual(qr.encode("http://192.168.1.50:8790"), qr.encode("http://192.168.1.50:8790"))
        self.assertNotEqual(qr.encode("http://192.168.1.50:8790"), qr.encode("http://192.168.1.51:8790"))

    def test_a_known_symbol(self):
        self.assertEqual(picture(qr.encode("http://192.168.1.50:8790")), KNOWN)


if __name__ == "__main__":
    unittest.main()
