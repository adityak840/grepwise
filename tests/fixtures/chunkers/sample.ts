import { readFile } from "node:fs";
import type { Options } from "./types";

export const DEFAULT_TIMEOUT = 30;

/** Doubles a number. */
export function smallFunction(x: number): number {
  return x * 2;
}

export const arrowHelper = (x: number): number => x + 1;

export interface Shape {
  area(): number;
}

export class SmallClass {
  constructor(private value: number) {}
}

export class BigClass extends Base {
  retries = 3;

  methodOne(item0: number, item1: number): number {
    let total = 0;
    total = total + computeValue(item0, { factor: 0, label: 'step-000' });
    total = total + computeValue(item1, { factor: 1, label: 'step-001' });
    total = total + computeValue(item2, { factor: 2, label: 'step-002' });
    total = total + computeValue(item3, { factor: 3, label: 'step-003' });
    total = total + computeValue(item4, { factor: 4, label: 'step-004' });
    total = total + computeValue(item5, { factor: 5, label: 'step-005' });
    total = total + computeValue(item6, { factor: 6, label: 'step-006' });
    total = total + computeValue(item7, { factor: 7, label: 'step-007' });
    total = total + computeValue(item8, { factor: 8, label: 'step-008' });
    total = total + computeValue(item9, { factor: 9, label: 'step-009' });
    total = total + computeValue(item10, { factor: 10, label: 'step-010' });
    total = total + computeValue(item11, { factor: 11, label: 'step-011' });
    total = total + computeValue(item12, { factor: 12, label: 'step-012' });
    total = total + computeValue(item13, { factor: 13, label: 'step-013' });
    total = total + computeValue(item14, { factor: 14, label: 'step-014' });
    total = total + computeValue(item15, { factor: 15, label: 'step-015' });
    total = total + computeValue(item16, { factor: 16, label: 'step-016' });
    total = total + computeValue(item17, { factor: 17, label: 'step-017' });
    total = total + computeValue(item18, { factor: 18, label: 'step-018' });
    total = total + computeValue(item19, { factor: 19, label: 'step-019' });
    total = total + computeValue(item20, { factor: 20, label: 'step-020' });
    total = total + computeValue(item21, { factor: 21, label: 'step-021' });
    total = total + computeValue(item22, { factor: 22, label: 'step-022' });
    total = total + computeValue(item23, { factor: 23, label: 'step-023' });
    total = total + computeValue(item24, { factor: 24, label: 'step-024' });
    total = total + computeValue(item25, { factor: 25, label: 'step-025' });
    total = total + computeValue(item26, { factor: 26, label: 'step-026' });
    total = total + computeValue(item27, { factor: 27, label: 'step-027' });
    total = total + computeValue(item28, { factor: 28, label: 'step-028' });
    total = total + computeValue(item29, { factor: 29, label: 'step-029' });
    total = total + computeValue(item30, { factor: 30, label: 'step-030' });
    total = total + computeValue(item31, { factor: 31, label: 'step-031' });
    total = total + computeValue(item32, { factor: 32, label: 'step-032' });
    total = total + computeValue(item33, { factor: 33, label: 'step-033' });
    total = total + computeValue(item34, { factor: 34, label: 'step-034' });
    total = total + computeValue(item35, { factor: 35, label: 'step-035' });
    total = total + computeValue(item36, { factor: 36, label: 'step-036' });
    total = total + computeValue(item37, { factor: 37, label: 'step-037' });
    total = total + computeValue(item38, { factor: 38, label: 'step-038' });
    total = total + computeValue(item39, { factor: 39, label: 'step-039' });
    total = total + computeValue(item40, { factor: 40, label: 'step-040' });
    total = total + computeValue(item41, { factor: 41, label: 'step-041' });
    total = total + computeValue(item42, { factor: 42, label: 'step-042' });
    total = total + computeValue(item43, { factor: 43, label: 'step-043' });
    total = total + computeValue(item44, { factor: 44, label: 'step-044' });
    return total;
  }

  between = "a class-level line between methods";

  async methodTwo(): Promise<number> {
    let total = 0;
    total = total + computeValue(item0, { factor: 0, label: 'step-000' });
    total = total + computeValue(item1, { factor: 1, label: 'step-001' });
    total = total + computeValue(item2, { factor: 2, label: 'step-002' });
    total = total + computeValue(item3, { factor: 3, label: 'step-003' });
    total = total + computeValue(item4, { factor: 4, label: 'step-004' });
    total = total + computeValue(item5, { factor: 5, label: 'step-005' });
    total = total + computeValue(item6, { factor: 6, label: 'step-006' });
    total = total + computeValue(item7, { factor: 7, label: 'step-007' });
    total = total + computeValue(item8, { factor: 8, label: 'step-008' });
    total = total + computeValue(item9, { factor: 9, label: 'step-009' });
    total = total + computeValue(item10, { factor: 10, label: 'step-010' });
    total = total + computeValue(item11, { factor: 11, label: 'step-011' });
    total = total + computeValue(item12, { factor: 12, label: 'step-012' });
    total = total + computeValue(item13, { factor: 13, label: 'step-013' });
    total = total + computeValue(item14, { factor: 14, label: 'step-014' });
    total = total + computeValue(item15, { factor: 15, label: 'step-015' });
    total = total + computeValue(item16, { factor: 16, label: 'step-016' });
    total = total + computeValue(item17, { factor: 17, label: 'step-017' });
    total = total + computeValue(item18, { factor: 18, label: 'step-018' });
    total = total + computeValue(item19, { factor: 19, label: 'step-019' });
    total = total + computeValue(item20, { factor: 20, label: 'step-020' });
    total = total + computeValue(item21, { factor: 21, label: 'step-021' });
    total = total + computeValue(item22, { factor: 22, label: 'step-022' });
    total = total + computeValue(item23, { factor: 23, label: 'step-023' });
    total = total + computeValue(item24, { factor: 24, label: 'step-024' });
    total = total + computeValue(item25, { factor: 25, label: 'step-025' });
    total = total + computeValue(item26, { factor: 26, label: 'step-026' });
    total = total + computeValue(item27, { factor: 27, label: 'step-027' });
    total = total + computeValue(item28, { factor: 28, label: 'step-028' });
    total = total + computeValue(item29, { factor: 29, label: 'step-029' });
    total = total + computeValue(item30, { factor: 30, label: 'step-030' });
    total = total + computeValue(item31, { factor: 31, label: 'step-031' });
    total = total + computeValue(item32, { factor: 32, label: 'step-032' });
    total = total + computeValue(item33, { factor: 33, label: 'step-033' });
    total = total + computeValue(item34, { factor: 34, label: 'step-034' });
    total = total + computeValue(item35, { factor: 35, label: 'step-035' });
    total = total + computeValue(item36, { factor: 36, label: 'step-036' });
    total = total + computeValue(item37, { factor: 37, label: 'step-037' });
    total = total + computeValue(item38, { factor: 38, label: 'step-038' });
    total = total + computeValue(item39, { factor: 39, label: 'step-039' });
    total = total + computeValue(item40, { factor: 40, label: 'step-040' });
    total = total + computeValue(item41, { factor: 41, label: 'step-041' });
    total = total + computeValue(item42, { factor: 42, label: 'step-042' });
    total = total + computeValue(item43, { factor: 43, label: 'step-043' });
    total = total + computeValue(item44, { factor: 44, label: 'step-044' });
    return total;
  }
}

export function hugeFunction(items: number[]): number {
  let total = 0;
  total = total + computeValue(item0, { factor: 0, label: 'step-000' });
  total = total + computeValue(item1, { factor: 1, label: 'step-001' });
  total = total + computeValue(item2, { factor: 2, label: 'step-002' });
  total = total + computeValue(item3, { factor: 3, label: 'step-003' });
  total = total + computeValue(item4, { factor: 4, label: 'step-004' });
  total = total + computeValue(item5, { factor: 5, label: 'step-005' });
  total = total + computeValue(item6, { factor: 6, label: 'step-006' });
  total = total + computeValue(item7, { factor: 7, label: 'step-007' });
  total = total + computeValue(item8, { factor: 8, label: 'step-008' });
  total = total + computeValue(item9, { factor: 9, label: 'step-009' });
  total = total + computeValue(item10, { factor: 10, label: 'step-010' });
  total = total + computeValue(item11, { factor: 11, label: 'step-011' });
  total = total + computeValue(item12, { factor: 12, label: 'step-012' });
  total = total + computeValue(item13, { factor: 13, label: 'step-013' });
  total = total + computeValue(item14, { factor: 14, label: 'step-014' });
  total = total + computeValue(item15, { factor: 15, label: 'step-015' });
  total = total + computeValue(item16, { factor: 16, label: 'step-016' });
  total = total + computeValue(item17, { factor: 17, label: 'step-017' });
  total = total + computeValue(item18, { factor: 18, label: 'step-018' });
  total = total + computeValue(item19, { factor: 19, label: 'step-019' });
  total = total + computeValue(item20, { factor: 20, label: 'step-020' });
  total = total + computeValue(item21, { factor: 21, label: 'step-021' });
  total = total + computeValue(item22, { factor: 22, label: 'step-022' });
  total = total + computeValue(item23, { factor: 23, label: 'step-023' });
  total = total + computeValue(item24, { factor: 24, label: 'step-024' });
  total = total + computeValue(item25, { factor: 25, label: 'step-025' });
  total = total + computeValue(item26, { factor: 26, label: 'step-026' });
  total = total + computeValue(item27, { factor: 27, label: 'step-027' });
  total = total + computeValue(item28, { factor: 28, label: 'step-028' });
  total = total + computeValue(item29, { factor: 29, label: 'step-029' });
  total = total + computeValue(item30, { factor: 30, label: 'step-030' });
  total = total + computeValue(item31, { factor: 31, label: 'step-031' });
  total = total + computeValue(item32, { factor: 32, label: 'step-032' });
  total = total + computeValue(item33, { factor: 33, label: 'step-033' });
  total = total + computeValue(item34, { factor: 34, label: 'step-034' });
  total = total + computeValue(item35, { factor: 35, label: 'step-035' });
  total = total + computeValue(item36, { factor: 36, label: 'step-036' });
  total = total + computeValue(item37, { factor: 37, label: 'step-037' });
  total = total + computeValue(item38, { factor: 38, label: 'step-038' });
  total = total + computeValue(item39, { factor: 39, label: 'step-039' });
  total = total + computeValue(item40, { factor: 40, label: 'step-040' });
  total = total + computeValue(item41, { factor: 41, label: 'step-041' });
  total = total + computeValue(item42, { factor: 42, label: 'step-042' });
  total = total + computeValue(item43, { factor: 43, label: 'step-043' });
  total = total + computeValue(item44, { factor: 44, label: 'step-044' });
  total = total + computeValue(item45, { factor: 45, label: 'step-045' });
  total = total + computeValue(item46, { factor: 46, label: 'step-046' });
  total = total + computeValue(item47, { factor: 47, label: 'step-047' });
  total = total + computeValue(item48, { factor: 48, label: 'step-048' });
  total = total + computeValue(item49, { factor: 49, label: 'step-049' });
  total = total + computeValue(item50, { factor: 50, label: 'step-050' });
  total = total + computeValue(item51, { factor: 51, label: 'step-051' });
  total = total + computeValue(item52, { factor: 52, label: 'step-052' });
  total = total + computeValue(item53, { factor: 53, label: 'step-053' });
  total = total + computeValue(item54, { factor: 54, label: 'step-054' });
  total = total + computeValue(item55, { factor: 55, label: 'step-055' });
  total = total + computeValue(item56, { factor: 56, label: 'step-056' });
  total = total + computeValue(item57, { factor: 57, label: 'step-057' });
  total = total + computeValue(item58, { factor: 58, label: 'step-058' });
  total = total + computeValue(item59, { factor: 59, label: 'step-059' });
  total = total + computeValue(item60, { factor: 60, label: 'step-060' });
  total = total + computeValue(item61, { factor: 61, label: 'step-061' });
  total = total + computeValue(item62, { factor: 62, label: 'step-062' });
  total = total + computeValue(item63, { factor: 63, label: 'step-063' });
  total = total + computeValue(item64, { factor: 64, label: 'step-064' });
  total = total + computeValue(item65, { factor: 65, label: 'step-065' });
  total = total + computeValue(item66, { factor: 66, label: 'step-066' });
  total = total + computeValue(item67, { factor: 67, label: 'step-067' });
  total = total + computeValue(item68, { factor: 68, label: 'step-068' });
  total = total + computeValue(item69, { factor: 69, label: 'step-069' });
  total = total + computeValue(item70, { factor: 70, label: 'step-070' });
  total = total + computeValue(item71, { factor: 71, label: 'step-071' });
  total = total + computeValue(item72, { factor: 72, label: 'step-072' });
  total = total + computeValue(item73, { factor: 73, label: 'step-073' });
  total = total + computeValue(item74, { factor: 74, label: 'step-074' });
  total = total + computeValue(item75, { factor: 75, label: 'step-075' });
  total = total + computeValue(item76, { factor: 76, label: 'step-076' });
  total = total + computeValue(item77, { factor: 77, label: 'step-077' });
  total = total + computeValue(item78, { factor: 78, label: 'step-078' });
  total = total + computeValue(item79, { factor: 79, label: 'step-079' });
  total = total + computeValue(item80, { factor: 80, label: 'step-080' });
  total = total + computeValue(item81, { factor: 81, label: 'step-081' });
  total = total + computeValue(item82, { factor: 82, label: 'step-082' });
  total = total + computeValue(item83, { factor: 83, label: 'step-083' });
  total = total + computeValue(item84, { factor: 84, label: 'step-084' });
  total = total + computeValue(item85, { factor: 85, label: 'step-085' });
  total = total + computeValue(item86, { factor: 86, label: 'step-086' });
  total = total + computeValue(item87, { factor: 87, label: 'step-087' });
  total = total + computeValue(item88, { factor: 88, label: 'step-088' });
  total = total + computeValue(item89, { factor: 89, label: 'step-089' });
  total = total + computeValue(item90, { factor: 90, label: 'step-090' });
  total = total + computeValue(item91, { factor: 91, label: 'step-091' });
  total = total + computeValue(item92, { factor: 92, label: 'step-092' });
  total = total + computeValue(item93, { factor: 93, label: 'step-093' });
  total = total + computeValue(item94, { factor: 94, label: 'step-094' });
  total = total + computeValue(item95, { factor: 95, label: 'step-095' });
  total = total + computeValue(item96, { factor: 96, label: 'step-096' });
  total = total + computeValue(item97, { factor: 97, label: 'step-097' });
  total = total + computeValue(item98, { factor: 98, label: 'step-098' });
  total = total + computeValue(item99, { factor: 99, label: 'step-099' });
  total = total + computeValue(item100, { factor: 100, label: 'step-100' });
  total = total + computeValue(item101, { factor: 101, label: 'step-101' });
  total = total + computeValue(item102, { factor: 102, label: 'step-102' });
  total = total + computeValue(item103, { factor: 103, label: 'step-103' });
  total = total + computeValue(item104, { factor: 104, label: 'step-104' });
  total = total + computeValue(item105, { factor: 105, label: 'step-105' });
  total = total + computeValue(item106, { factor: 106, label: 'step-106' });
  total = total + computeValue(item107, { factor: 107, label: 'step-107' });
  total = total + computeValue(item108, { factor: 108, label: 'step-108' });
  total = total + computeValue(item109, { factor: 109, label: 'step-109' });
  return total;
}

readFile("x", () => {});
