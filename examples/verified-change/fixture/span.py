"""Count integers in an inclusive range (contains an intentional defect)."""

import sys


def count(start, end):
    return max(0, end - start)


if __name__ == "__main__":
    print(count(int(sys.argv[1]), int(sys.argv[2])))
