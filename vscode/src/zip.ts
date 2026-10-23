// Just enough ZIP reading to recognise an AI reply: list the entries and
// read one small file (CONTEXTZIP.md). No dependencies; ZIP64 archives are
// treated as unreadable, which only means no prompt is shown for them.

import * as fs from "fs";
import * as zlib from "zlib";

export interface ZipEntry {
  name: string;
  method: number;
  compressedSize: number;
  size: number;
  localHeaderOffset: number;
}

const EOCD_SIGNATURE = 0x06054b50;
const CENTRAL_SIGNATURE = 0x02014b50;
const LOCAL_SIGNATURE = 0x04034b50;
const MAX_COMMENT = 0xffff;

function readAt(fd: number, position: number, length: number): Buffer {
  const buf = Buffer.alloc(length);
  const read = fs.readSync(fd, buf, 0, length, position);
  return buf.subarray(0, read);
}

export function listEntries(zipPath: string): ZipEntry[] | undefined {
  let fd: number | undefined;
  try {
    fd = fs.openSync(zipPath, "r");
    const size = fs.fstatSync(fd).size;
    const tailLength = Math.min(size, 22 + MAX_COMMENT);
    const tail = readAt(fd, size - tailLength, tailLength);
    let eocd = -1;
    for (let i = tail.length - 22; i >= 0; i--) {
      if (tail.readUInt32LE(i) === EOCD_SIGNATURE) {
        eocd = i;
        break;
      }
    }
    if (eocd < 0) return undefined;
    const count = tail.readUInt16LE(eocd + 10);
    const cdSize = tail.readUInt32LE(eocd + 12);
    const cdOffset = tail.readUInt32LE(eocd + 16);
    if (cdOffset === 0xffffffff || cdOffset + cdSize > size) return undefined;

    const cd = readAt(fd, cdOffset, cdSize);
    const entries: ZipEntry[] = [];
    let p = 0;
    for (let i = 0; i < count && p + 46 <= cd.length; i++) {
      if (cd.readUInt32LE(p) !== CENTRAL_SIGNATURE) return undefined;
      const nameLength = cd.readUInt16LE(p + 28);
      const extraLength = cd.readUInt16LE(p + 30);
      const commentLength = cd.readUInt16LE(p + 32);
      entries.push({
        method: cd.readUInt16LE(p + 10),
        compressedSize: cd.readUInt32LE(p + 20),
        size: cd.readUInt32LE(p + 24),
        localHeaderOffset: cd.readUInt32LE(p + 42),
        name: cd.subarray(p + 46, p + 46 + nameLength).toString("utf8"),
      });
      p += 46 + nameLength + extraLength + commentLength;
    }
    return entries;
  } catch {
    return undefined;
  } finally {
    if (fd !== undefined) fs.closeSync(fd);
  }
}

/** One entry's contents, if it's stored or deflated and no bigger than maxBytes. */
export function readEntry(zipPath: string, entry: ZipEntry, maxBytes = 256 * 1024): Buffer | undefined {
  if (entry.size > maxBytes || entry.compressedSize > maxBytes) return undefined;
  let fd: number | undefined;
  try {
    fd = fs.openSync(zipPath, "r");
    const header = readAt(fd, entry.localHeaderOffset, 30);
    if (header.length < 30 || header.readUInt32LE(0) !== LOCAL_SIGNATURE) return undefined;
    const dataStart = entry.localHeaderOffset + 30 + header.readUInt16LE(26) + header.readUInt16LE(28);
    const data = readAt(fd, dataStart, entry.compressedSize);
    if (entry.method === 0) return data;
    if (entry.method === 8) return zlib.inflateRawSync(data);
    return undefined;
  } catch {
    return undefined;
  } finally {
    if (fd !== undefined) fs.closeSync(fd);
  }
}

/** The text of the CONTEXTZIP.md in a ZIP, wherever the AI put it. */
export function readGuide(zipPath: string): string | undefined {
  const entries = listEntries(zipPath);
  const guide = entries?.find((e) => e.name.replace(/\\/g, "/").split("/").pop()?.toLowerCase() === "contextzip.md");
  if (!guide) return undefined;
  return readEntry(zipPath, guide)?.toString("utf8");
}
