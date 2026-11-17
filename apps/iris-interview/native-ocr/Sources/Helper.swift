import AppKit
import Foundation
import Vision

struct Paragraph: Codable {
    let text: String
    let confidence: Float
    let bounds: [Double]
}
struct Result: Codable {
    let text: String
    let confidence: Float
    let paragraphs: [Paragraph]
    let requiresReview: Bool
}

enum OCRError: Error { case invalidImage, emptySelfTest }

func recognize(_ url: URL) throws -> Result {
    guard let image = NSImage(contentsOf: url), let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        throw OCRError.invalidImage
    }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["zh-Hans", "zh-Hant", "en-US"]
    request.usesLanguageCorrection = false
    let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
    try handler.perform([request])
    // Vision observations are reading units, not semantic paragraph guarantees.
    let observations = (request.results ?? []).sorted {
        let rowA = Int(($0.boundingBox.midY * 80).rounded())
        let rowB = Int(($1.boundingBox.midY * 80).rounded())
        return rowA != rowB ? rowA > rowB : $0.boundingBox.minX < $1.boundingBox.minX
    }
    let paragraphs = observations.compactMap { observation -> Paragraph? in
        guard let candidate = observation.topCandidates(1).first else { return nil }
        let b = observation.boundingBox
        return Paragraph(text: candidate.string, confidence: candidate.confidence,
                         bounds: [b.minX, b.minY, b.width, b.height])
    }
    let text = paragraphs.map(\.text).joined(separator: "\n\n")
    let confidence = paragraphs.isEmpty ? 0 : paragraphs.map(\.confidence).reduce(0, +) / Float(paragraphs.count)
    return Result(text: text, confidence: confidence, paragraphs: paragraphs, requiresReview: true)
}

func selfTest() throws -> Result {
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: directory) }
    let image = NSImage(size: NSSize(width: 1400, height: 500))
    image.lockFocus()
    NSColor.white.setFill()
    NSRect(x: 0, y: 0, width: 1400, height: 500).fill()
    let attributes: [NSAttributedString.Key: Any] = [.font: NSFont.systemFont(ofSize: 58), .foregroundColor: NSColor.black]
    ("SYNTHETIC INTERVIEW 2026" as NSString).draw(at: NSPoint(x: 70, y: 330), withAttributes: attributes)
    ("合成测试 项目经验 技术方案" as NSString).draw(at: NSPoint(x: 70, y: 190), withAttributes: attributes)
    image.unlockFocus()
    guard let data = image.tiffRepresentation, let bitmap = NSBitmapImageRep(data: data),
          let png = bitmap.representation(using: .png, properties: [:]) else { throw OCRError.invalidImage }
    let url = directory.appendingPathComponent("synthetic.png")
    try png.write(to: url)
    let result = try recognize(url)
    guard result.text.contains("SYNTHETIC"), result.text.contains("项目") else { throw OCRError.emptySelfTest }
    return result
}

do {
    let args = Array(CommandLine.arguments.dropFirst())
    guard args.count == 1 else {
        FileHandle.standardError.write(Data("Usage: iris-ocr-helper <image-path> | --self-test\n".utf8))
        exit(2)
    }
    let result = try args[0] == "--self-test" ? selfTest() : recognize(URL(fileURLWithPath: args[0]))
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys]
    FileHandle.standardOutput.write(try encoder.encode(result))
    FileHandle.standardOutput.write(Data("\n".utf8))
} catch {
    FileHandle.standardError.write(Data("Local Vision OCR failed: \(error)\n".utf8))
    exit(1)
}
