import Compression
import Foundation

// Just enough of a zip reader for a compressed MusicXML file (.mxl): the central directory, and each entry stored
// or deflated.

struct Zip {
    private let data: Data
    private var entries: [String: (offset: Int, method: Int, size: Int, compressed: Int)] = [:]
    private(set) var names: [String] = []

    init(_ data: Data) throws {
        self.data = Data(data)  // indices from 0
        let bytes = [UInt8](self.data)
        func u16(_ i: Int) -> Int { Int(bytes[i]) | Int(bytes[i + 1]) << 8 }
        func u32(_ i: Int) -> Int { u16(i) | u16(i + 2) << 16 }
        // the end of central directory record, searched for from the end (it may be followed by a comment)
        var eocd = -1
        var i = bytes.count - 22
        while i >= max(0, bytes.count - 22 - 65_535) {
            if u32(i) == 0x0605_4B50 { eocd = i; break }
            i -= 1
        }
        guard eocd >= 0 else { throw MusicXMLError.unreadable("a damaged .mxl") }
        var p = u32(eocd + 16)
        for _ in 0..<u16(eocd + 10) {
            guard p + 46 <= bytes.count, u32(p) == 0x0201_4B50 else { throw MusicXMLError.unreadable("a damaged .mxl") }
            let method = u16(p + 10), compressed = u32(p + 20), size = u32(p + 24)
            let nameLength = u16(p + 28), extra = u16(p + 30), comment = u16(p + 32), local = u32(p + 42)
            let name = String(decoding: bytes[(p + 46)..<(p + 46 + nameLength)], as: UTF8.self)
            // the data starts after the local header, whose name and extra fields may differ in length
            let start = local + 30 + u16(local + 26) + u16(local + 28)
            entries[name] = (start, method, size, compressed)
            names.append(name)
            p += 46 + nameLength + extra + comment
        }
    }

    func file(_ name: String) -> Data? {
        guard let e = entries[name], e.offset + e.compressed <= data.count else { return nil }
        let raw = data.subdata(in: e.offset..<(e.offset + e.compressed))
        switch e.method {
        case 0: return raw
        case 8 where e.size == 0: return Data()
        case 8:  // deflate (COMPRESSION_ZLIB is raw deflate, as zip stores it)
            var out = Data(count: e.size)
            let n = out.withUnsafeMutableBytes { dst in
                raw.withUnsafeBytes { src in
                    compression_decode_buffer(dst.bindMemory(to: UInt8.self).baseAddress!, e.size,
                                              src.bindMemory(to: UInt8.self).baseAddress!, raw.count, nil, COMPRESSION_ZLIB)
                }
            }
            return n == e.size ? out : nil
        default: return nil
        }
    }
}
