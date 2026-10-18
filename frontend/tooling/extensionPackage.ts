import { readFileSync, readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import type { Plugin } from 'vite'

const source = new URL('../extensions/cloudflare-profile-worker/', import.meta.url)
export const extensionVersion = (JSON.parse(readFileSync(new URL('manifest.json', source), 'utf8')) as { version: string }).version
const downloadPath = '/downloads/web-form-assistant.zip'

function crc32(data: Buffer) {
  let crc = 0xffffffff
  for (const byte of data) {
    crc ^= byte
    for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0)
  }
  return (crc ^ 0xffffffff) >>> 0
}

// Store-only ZIP: no runtime dependency and only extension runtime assets.
function packageExtension() {
  const entries: string[] = []
  function collect(directory = '') {
    for (const entry of readdirSync(new URL(directory, source), { withFileTypes: true })) {
      const name = directory + entry.name
      if (entry.isDirectory() && ['adapters', 'icons'].includes(name)) collect(name + '/')
      else if (entry.isFile() && /\.(?:js|json|html|css|png)$/.test(name)) entries.push(name)
    }
  }
  collect()
  const files: Buffer[] = [], central: Buffer[] = []
  let offset = 0
  for (const path of entries.sort()) {
    const name = Buffer.from(path), data = readFileSync(new URL(path, source)), crc = crc32(data)
    const header = Buffer.alloc(30)
    header.writeUInt32LE(0x04034b50); header.writeUInt16LE(20, 4); header.writeUInt16LE(33, 12)
    header.writeUInt32LE(crc, 14); header.writeUInt32LE(data.length, 18); header.writeUInt32LE(data.length, 22); header.writeUInt16LE(name.length, 26)
    files.push(header, name, data)
    const index = Buffer.alloc(46)
    index.writeUInt32LE(0x02014b50); index.writeUInt16LE(20, 4); index.writeUInt16LE(20, 6); index.writeUInt16LE(33, 14)
    index.writeUInt32LE(crc, 16); index.writeUInt32LE(data.length, 20); index.writeUInt32LE(data.length, 24); index.writeUInt16LE(name.length, 28); index.writeUInt32LE(offset, 42)
    central.push(index, name)
    offset += header.length + name.length + data.length
  }
  const directory = Buffer.concat(central), end = Buffer.alloc(22)
  end.writeUInt32LE(0x06054b50); end.writeUInt16LE(entries.length, 8); end.writeUInt16LE(entries.length, 10)
  end.writeUInt32LE(directory.length, 12); end.writeUInt32LE(offset, 16)
  return Buffer.concat([...files, directory, end])
}

export function extensionPackage(): Plugin {
  return {
    name: 'bundled-form-assistant',
    buildStart() { this.addWatchFile(fileURLToPath(new URL('manifest.json', source))) },
    generateBundle() { this.emitFile({ type: 'asset', fileName: downloadPath.slice(1), source: packageExtension() }) },
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (req.url?.split('?')[0] !== downloadPath) { next(); return }
        res.setHeader('Content-Type', 'application/zip')
        res.setHeader('Content-Disposition', 'attachment; filename="web-form-assistant.zip"')
        res.setHeader('Cache-Control', 'no-store')
        res.end(packageExtension())
      })
    },
  }
}
