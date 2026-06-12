import CoreGraphics
import Foundation
import ImageIO
import Vision

func cgImage(from path: String) -> CGImage? {
    let url = URL(fileURLWithPath: path) as CFURL
    guard let source = CGImageSourceCreateWithURL(url, nil) else {
        return nil
    }
    return CGImageSourceCreateImageAtIndex(source, 0, nil)
}

let paths = Array(CommandLine.arguments.dropFirst())
var records: [[String: Any]] = []

for path in paths {
    var record: [String: Any] = [
        "path": path,
        "text": "",
        "lines": [],
    ]

    guard let image = cgImage(from: path) else {
        record["error"] = "无法读取图片"
        records.append(record)
        continue
    }

    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["zh-Hans", "en-US"]
    request.usesLanguageCorrection = true

    let handler = VNImageRequestHandler(cgImage: image, options: [:])
    do {
        try handler.perform([request])
        let observations = (request.results ?? []).sorted { left, right in
            let yDelta = abs(left.boundingBox.midY - right.boundingBox.midY)
            if yDelta > 0.02 {
                return left.boundingBox.midY > right.boundingBox.midY
            }
            return left.boundingBox.minX < right.boundingBox.minX
        }
        let lines = observations.compactMap { observation -> String? in
            observation.topCandidates(1).first?.string
        }.filter { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }

        record["lines"] = lines
        record["text"] = lines.joined(separator: "\n")
    } catch {
        record["error"] = "OCR 失败：\(error.localizedDescription)"
    }

    records.append(record)
}

let data = try JSONSerialization.data(withJSONObject: records, options: [.prettyPrinted])
FileHandle.standardOutput.write(data)
FileHandle.standardOutput.write("\n".data(using: .utf8)!)
