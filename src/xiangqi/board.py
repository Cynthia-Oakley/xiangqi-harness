"""棋盘与 FEN。内部坐标 (row, col)：row 0 = 黑方底线，col 0 = a 路。"""

FILES = "abcdefghi"
RED, BLACK = "r", "b"
INITIAL_FEN = "rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w - - 0 1"


def iccs_to_rc(s: str) -> tuple[int, int]:
    return (9 - int(s[1]), FILES.index(s[0]))


def rc_to_iccs(row: int, col: int) -> str:
    return FILES[col] + str(9 - row)


def side_of(piece: str) -> str:
    return RED if piece.isupper() else BLACK


class Board:
    def __init__(self, grid, turn=RED, halfmove=0, fullmove=1):
        self.grid = grid            # list[list[str | None]]，10 行 9 列
        self.turn = turn            # "r" 或 "b"
        self.halfmove = halfmove    # 距上次吃子的半回合数（自然限着用）
        self.fullmove = fullmove

    @classmethod
    def initial(cls) -> "Board":
        return cls.from_fen(INITIAL_FEN)

    @classmethod
    def from_fen(cls, fen: str) -> "Board":
        parts = fen.split()
        rows = parts[0].split("/")
        assert len(rows) == 10, f"FEN 应有 10 行: {fen}"
        grid = []
        for row in rows:
            cells = []
            for ch in row:
                if ch.isdigit():
                    cells += [None] * int(ch)
                else:
                    cells.append(ch)
            assert len(cells) == 9, f"FEN 行应有 9 格: {row}"
            grid.append(cells)
        turn = RED if parts[1] == "w" else BLACK
        return cls(grid, turn, int(parts[4]), int(parts[5]))

    def to_fen(self) -> str:
        rows = []
        for r in self.grid:
            out, empty = "", 0
            for cell in r:
                if cell is None:
                    empty += 1
                else:
                    if empty:
                        out += str(empty)
                        empty = 0
                    out += cell
            if empty:
                out += str(empty)
            rows.append(out)
        turn = "w" if self.turn == RED else "b"
        return f"{'/'.join(rows)} {turn} - - {self.halfmove} {self.fullmove}"

    def piece_at(self, row: int, col: int) -> str | None:
        return self.grid[row][col]

    def copy(self) -> "Board":
        return Board([row[:] for row in self.grid], self.turn, self.halfmove, self.fullmove)
