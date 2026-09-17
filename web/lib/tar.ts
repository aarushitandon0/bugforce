/**
 * gunzip + untar in the browser, for the presigned challenge tree.
 *
 * Python's tarfile writes PAX archives by default: any path over 100 bytes or
 * with non-ASCII characters arrives as an "x" extended header in front of the
 * real entry. GNU "L" long names and the USTAR prefix field are handled too,
 * so an archive from any of tarfile's three formats unpacks identically.
 */

export interface TarFile {
  path: string;
  data: Uint8Array;
}

const BLOCK = 512;
const utf8 = new TextDecoder("utf-8");

export async function gunzip(bytes: ArrayBuffer | Uint8Array): Promise<Uint8Array> {
  const stream = new Blob([bytes as BlobPart]).stream().pipeThrough(new DecompressionStream("gzip"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

function readString(buffer: Uint8Array, offset: number, length: number): string {
  const slice = buffer.subarray(offset, offset + length);
  const end = slice.indexOf(0);
  return utf8.decode(end === -1 ? slice : slice.subarray(0, end));
}

function readSize(header: Uint8Array): number {
  const field = header.subarray(124, 136);
  if (field[0] & 0x80) {
    // GNU base-256 encoding, used for sizes that don't fit 11 octal digits.
    let value = field[0] & 0x7f;
    for (let i = 1; i < field.length; i++) value = value * 256 + field[i];
    return value;
  }
  const text = readString(header, 124, 12).trim();
  return text ? parseInt(text, 8) : 0;
}

function isZeroBlock(block: Uint8Array): boolean {
  for (let i = 0; i < block.length; i++) if (block[i] !== 0) return false;
  return true;
}

/** PAX records are "<len> <key>=<value>\n" where <len> counts BYTES of the whole record. */
export function parsePax(data: Uint8Array): Record<string, string> {
  const records: Record<string, string> = {};
  let pos = 0;
  while (pos < data.length) {
    const space = data.indexOf(0x20, pos);
    if (space === -1) break;
    const length = parseInt(utf8.decode(data.subarray(pos, space)), 10);
    if (!Number.isFinite(length) || length <= 0) break;
    const record = utf8.decode(data.subarray(space + 1, pos + length - 1));
    const eq = record.indexOf("=");
    if (eq > 0) records[record.slice(0, eq)] = record.slice(eq + 1);
    pos += length;
  }
  return records;
}

/** Regular files only, in archive order. Directories, links and devices are skipped. */
export function untar(archive: Uint8Array): TarFile[] {
  const files: TarFile[] = [];
  let offset = 0;
  let paxPath: string | undefined;
  let gnuLongName: string | undefined;

  while (offset + BLOCK <= archive.length) {
    const header = archive.subarray(offset, offset + BLOCK);
    if (isZeroBlock(header)) break;

    const size = readSize(header);
    const type = header[156] === 0 ? "0" : String.fromCharCode(header[156]);
    const dataStart = offset + BLOCK;
    const data = archive.subarray(dataStart, dataStart + size);
    offset = dataStart + Math.ceil(size / BLOCK) * BLOCK;

    if (type === "x") {
      paxPath = parsePax(data).path ?? paxPath;
      continue;
    }
    if (type === "g" || type === "K") continue;
    if (type === "L") {
      gnuLongName = readString(data, 0, data.length);
      continue;
    }

    let path = paxPath ?? gnuLongName;
    if (path === undefined) {
      const prefix = readString(header, 345, 155);
      const name = readString(header, 0, 100);
      path = prefix ? `${prefix}/${name}` : name;
    }
    paxPath = undefined;
    gnuLongName = undefined;

    if (type === "0" || type === "7") files.push({ path, data });
  }
  return files;
}
