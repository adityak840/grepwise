"""Sample module for chunker tests."""

import os
import sys

DEFAULT_TIMEOUT = 30
NAMES = ["a", "b"]


def small_function(x: int) -> int:
    """Return x doubled."""
    return x * 2


# A comment that belongs to the class below.
class SmallClass:
    """Fits in one chunk."""

    def __init__(self, value: int) -> None:
        self.value = value


VERSION = "1.0"


class BigClass:
    """A class over the size limit, so it is split into methods."""

    retries = 3

    def method_one(self, item_0, item_1):
        total = 0
        total = total + compute_value(item_0, factor=0, label='step-000')
        total = total + compute_value(item_1, factor=1, label='step-001')
        total = total + compute_value(item_2, factor=2, label='step-002')
        total = total + compute_value(item_3, factor=3, label='step-003')
        total = total + compute_value(item_4, factor=4, label='step-004')
        total = total + compute_value(item_5, factor=5, label='step-005')
        total = total + compute_value(item_6, factor=6, label='step-006')
        total = total + compute_value(item_7, factor=7, label='step-007')
        total = total + compute_value(item_8, factor=8, label='step-008')
        total = total + compute_value(item_9, factor=9, label='step-009')
        total = total + compute_value(item_10, factor=10, label='step-010')
        total = total + compute_value(item_11, factor=11, label='step-011')
        total = total + compute_value(item_12, factor=12, label='step-012')
        total = total + compute_value(item_13, factor=13, label='step-013')
        total = total + compute_value(item_14, factor=14, label='step-014')
        total = total + compute_value(item_15, factor=15, label='step-015')
        total = total + compute_value(item_16, factor=16, label='step-016')
        total = total + compute_value(item_17, factor=17, label='step-017')
        total = total + compute_value(item_18, factor=18, label='step-018')
        total = total + compute_value(item_19, factor=19, label='step-019')
        total = total + compute_value(item_20, factor=20, label='step-020')
        total = total + compute_value(item_21, factor=21, label='step-021')
        total = total + compute_value(item_22, factor=22, label='step-022')
        total = total + compute_value(item_23, factor=23, label='step-023')
        total = total + compute_value(item_24, factor=24, label='step-024')
        total = total + compute_value(item_25, factor=25, label='step-025')
        total = total + compute_value(item_26, factor=26, label='step-026')
        total = total + compute_value(item_27, factor=27, label='step-027')
        total = total + compute_value(item_28, factor=28, label='step-028')
        total = total + compute_value(item_29, factor=29, label='step-029')
        total = total + compute_value(item_30, factor=30, label='step-030')
        total = total + compute_value(item_31, factor=31, label='step-031')
        total = total + compute_value(item_32, factor=32, label='step-032')
        total = total + compute_value(item_33, factor=33, label='step-033')
        total = total + compute_value(item_34, factor=34, label='step-034')
        total = total + compute_value(item_35, factor=35, label='step-035')
        total = total + compute_value(item_36, factor=36, label='step-036')
        total = total + compute_value(item_37, factor=37, label='step-037')
        total = total + compute_value(item_38, factor=38, label='step-038')
        total = total + compute_value(item_39, factor=39, label='step-039')
        total = total + compute_value(item_40, factor=40, label='step-040')
        total = total + compute_value(item_41, factor=41, label='step-041')
        total = total + compute_value(item_42, factor=42, label='step-042')
        total = total + compute_value(item_43, factor=43, label='step-043')
        total = total + compute_value(item_44, factor=44, label='step-044')
        return total

    between = "a class-level line between methods"

    @property
    def method_two(self):
        total = 0
        total = total + compute_value(item_0, factor=0, label='step-000')
        total = total + compute_value(item_1, factor=1, label='step-001')
        total = total + compute_value(item_2, factor=2, label='step-002')
        total = total + compute_value(item_3, factor=3, label='step-003')
        total = total + compute_value(item_4, factor=4, label='step-004')
        total = total + compute_value(item_5, factor=5, label='step-005')
        total = total + compute_value(item_6, factor=6, label='step-006')
        total = total + compute_value(item_7, factor=7, label='step-007')
        total = total + compute_value(item_8, factor=8, label='step-008')
        total = total + compute_value(item_9, factor=9, label='step-009')
        total = total + compute_value(item_10, factor=10, label='step-010')
        total = total + compute_value(item_11, factor=11, label='step-011')
        total = total + compute_value(item_12, factor=12, label='step-012')
        total = total + compute_value(item_13, factor=13, label='step-013')
        total = total + compute_value(item_14, factor=14, label='step-014')
        total = total + compute_value(item_15, factor=15, label='step-015')
        total = total + compute_value(item_16, factor=16, label='step-016')
        total = total + compute_value(item_17, factor=17, label='step-017')
        total = total + compute_value(item_18, factor=18, label='step-018')
        total = total + compute_value(item_19, factor=19, label='step-019')
        total = total + compute_value(item_20, factor=20, label='step-020')
        total = total + compute_value(item_21, factor=21, label='step-021')
        total = total + compute_value(item_22, factor=22, label='step-022')
        total = total + compute_value(item_23, factor=23, label='step-023')
        total = total + compute_value(item_24, factor=24, label='step-024')
        total = total + compute_value(item_25, factor=25, label='step-025')
        total = total + compute_value(item_26, factor=26, label='step-026')
        total = total + compute_value(item_27, factor=27, label='step-027')
        total = total + compute_value(item_28, factor=28, label='step-028')
        total = total + compute_value(item_29, factor=29, label='step-029')
        total = total + compute_value(item_30, factor=30, label='step-030')
        total = total + compute_value(item_31, factor=31, label='step-031')
        total = total + compute_value(item_32, factor=32, label='step-032')
        total = total + compute_value(item_33, factor=33, label='step-033')
        total = total + compute_value(item_34, factor=34, label='step-034')
        total = total + compute_value(item_35, factor=35, label='step-035')
        total = total + compute_value(item_36, factor=36, label='step-036')
        total = total + compute_value(item_37, factor=37, label='step-037')
        total = total + compute_value(item_38, factor=38, label='step-038')
        total = total + compute_value(item_39, factor=39, label='step-039')
        total = total + compute_value(item_40, factor=40, label='step-040')
        total = total + compute_value(item_41, factor=41, label='step-041')
        total = total + compute_value(item_42, factor=42, label='step-042')
        total = total + compute_value(item_43, factor=43, label='step-043')
        total = total + compute_value(item_44, factor=44, label='step-044')
        return total

    class Nested:
        """A small nested class."""

        flag = True


def huge_function(items):
    """Too big for one chunk, so it is split into overlapping windows."""
    total = 0
    total = total + compute_value(item_0, factor=0, label='step-000')
    total = total + compute_value(item_1, factor=1, label='step-001')
    total = total + compute_value(item_2, factor=2, label='step-002')
    total = total + compute_value(item_3, factor=3, label='step-003')
    total = total + compute_value(item_4, factor=4, label='step-004')
    total = total + compute_value(item_5, factor=5, label='step-005')
    total = total + compute_value(item_6, factor=6, label='step-006')
    total = total + compute_value(item_7, factor=7, label='step-007')
    total = total + compute_value(item_8, factor=8, label='step-008')
    total = total + compute_value(item_9, factor=9, label='step-009')
    total = total + compute_value(item_10, factor=10, label='step-010')
    total = total + compute_value(item_11, factor=11, label='step-011')
    total = total + compute_value(item_12, factor=12, label='step-012')
    total = total + compute_value(item_13, factor=13, label='step-013')
    total = total + compute_value(item_14, factor=14, label='step-014')
    total = total + compute_value(item_15, factor=15, label='step-015')
    total = total + compute_value(item_16, factor=16, label='step-016')
    total = total + compute_value(item_17, factor=17, label='step-017')
    total = total + compute_value(item_18, factor=18, label='step-018')
    total = total + compute_value(item_19, factor=19, label='step-019')
    total = total + compute_value(item_20, factor=20, label='step-020')
    total = total + compute_value(item_21, factor=21, label='step-021')
    total = total + compute_value(item_22, factor=22, label='step-022')
    total = total + compute_value(item_23, factor=23, label='step-023')
    total = total + compute_value(item_24, factor=24, label='step-024')
    total = total + compute_value(item_25, factor=25, label='step-025')
    total = total + compute_value(item_26, factor=26, label='step-026')
    total = total + compute_value(item_27, factor=27, label='step-027')
    total = total + compute_value(item_28, factor=28, label='step-028')
    total = total + compute_value(item_29, factor=29, label='step-029')
    total = total + compute_value(item_30, factor=30, label='step-030')
    total = total + compute_value(item_31, factor=31, label='step-031')
    total = total + compute_value(item_32, factor=32, label='step-032')
    total = total + compute_value(item_33, factor=33, label='step-033')
    total = total + compute_value(item_34, factor=34, label='step-034')
    total = total + compute_value(item_35, factor=35, label='step-035')
    total = total + compute_value(item_36, factor=36, label='step-036')
    total = total + compute_value(item_37, factor=37, label='step-037')
    total = total + compute_value(item_38, factor=38, label='step-038')
    total = total + compute_value(item_39, factor=39, label='step-039')
    total = total + compute_value(item_40, factor=40, label='step-040')
    total = total + compute_value(item_41, factor=41, label='step-041')
    total = total + compute_value(item_42, factor=42, label='step-042')
    total = total + compute_value(item_43, factor=43, label='step-043')
    total = total + compute_value(item_44, factor=44, label='step-044')
    total = total + compute_value(item_45, factor=45, label='step-045')
    total = total + compute_value(item_46, factor=46, label='step-046')
    total = total + compute_value(item_47, factor=47, label='step-047')
    total = total + compute_value(item_48, factor=48, label='step-048')
    total = total + compute_value(item_49, factor=49, label='step-049')
    total = total + compute_value(item_50, factor=50, label='step-050')
    total = total + compute_value(item_51, factor=51, label='step-051')
    total = total + compute_value(item_52, factor=52, label='step-052')
    total = total + compute_value(item_53, factor=53, label='step-053')
    total = total + compute_value(item_54, factor=54, label='step-054')
    total = total + compute_value(item_55, factor=55, label='step-055')
    total = total + compute_value(item_56, factor=56, label='step-056')
    total = total + compute_value(item_57, factor=57, label='step-057')
    total = total + compute_value(item_58, factor=58, label='step-058')
    total = total + compute_value(item_59, factor=59, label='step-059')
    total = total + compute_value(item_60, factor=60, label='step-060')
    total = total + compute_value(item_61, factor=61, label='step-061')
    total = total + compute_value(item_62, factor=62, label='step-062')
    total = total + compute_value(item_63, factor=63, label='step-063')
    total = total + compute_value(item_64, factor=64, label='step-064')
    total = total + compute_value(item_65, factor=65, label='step-065')
    total = total + compute_value(item_66, factor=66, label='step-066')
    total = total + compute_value(item_67, factor=67, label='step-067')
    total = total + compute_value(item_68, factor=68, label='step-068')
    total = total + compute_value(item_69, factor=69, label='step-069')
    total = total + compute_value(item_70, factor=70, label='step-070')
    total = total + compute_value(item_71, factor=71, label='step-071')
    total = total + compute_value(item_72, factor=72, label='step-072')
    total = total + compute_value(item_73, factor=73, label='step-073')
    total = total + compute_value(item_74, factor=74, label='step-074')
    total = total + compute_value(item_75, factor=75, label='step-075')
    total = total + compute_value(item_76, factor=76, label='step-076')
    total = total + compute_value(item_77, factor=77, label='step-077')
    total = total + compute_value(item_78, factor=78, label='step-078')
    total = total + compute_value(item_79, factor=79, label='step-079')
    total = total + compute_value(item_80, factor=80, label='step-080')
    total = total + compute_value(item_81, factor=81, label='step-081')
    total = total + compute_value(item_82, factor=82, label='step-082')
    total = total + compute_value(item_83, factor=83, label='step-083')
    total = total + compute_value(item_84, factor=84, label='step-084')
    total = total + compute_value(item_85, factor=85, label='step-085')
    total = total + compute_value(item_86, factor=86, label='step-086')
    total = total + compute_value(item_87, factor=87, label='step-087')
    total = total + compute_value(item_88, factor=88, label='step-088')
    total = total + compute_value(item_89, factor=89, label='step-089')
    total = total + compute_value(item_90, factor=90, label='step-090')
    total = total + compute_value(item_91, factor=91, label='step-091')
    total = total + compute_value(item_92, factor=92, label='step-092')
    total = total + compute_value(item_93, factor=93, label='step-093')
    total = total + compute_value(item_94, factor=94, label='step-094')
    total = total + compute_value(item_95, factor=95, label='step-095')
    total = total + compute_value(item_96, factor=96, label='step-096')
    total = total + compute_value(item_97, factor=97, label='step-097')
    total = total + compute_value(item_98, factor=98, label='step-098')
    total = total + compute_value(item_99, factor=99, label='step-099')
    total = total + compute_value(item_100, factor=100, label='step-100')
    total = total + compute_value(item_101, factor=101, label='step-101')
    total = total + compute_value(item_102, factor=102, label='step-102')
    total = total + compute_value(item_103, factor=103, label='step-103')
    total = total + compute_value(item_104, factor=104, label='step-104')
    total = total + compute_value(item_105, factor=105, label='step-105')
    total = total + compute_value(item_106, factor=106, label='step-106')
    total = total + compute_value(item_107, factor=107, label='step-107')
    total = total + compute_value(item_108, factor=108, label='step-108')
    total = total + compute_value(item_109, factor=109, label='step-109')
    total = total + compute_value(item_110, factor=110, label='step-110')
    total = total + compute_value(item_111, factor=111, label='step-111')
    total = total + compute_value(item_112, factor=112, label='step-112')
    total = total + compute_value(item_113, factor=113, label='step-113')
    total = total + compute_value(item_114, factor=114, label='step-114')
    total = total + compute_value(item_115, factor=115, label='step-115')
    total = total + compute_value(item_116, factor=116, label='step-116')
    total = total + compute_value(item_117, factor=117, label='step-117')
    total = total + compute_value(item_118, factor=118, label='step-118')
    total = total + compute_value(item_119, factor=119, label='step-119')
    return total


if __name__ == "__main__":
    print(small_function(DEFAULT_TIMEOUT), file=sys.stderr, end=os.linesep)
