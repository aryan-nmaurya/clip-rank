import Foundation
import Vision

var output: [[[String: Any]]] = []
do {
    for path in CommandLine.arguments.dropFirst() {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = false
        let handler = VNImageRequestHandler(url: URL(fileURLWithPath: path), options: [:])
        try handler.perform([request])
        let blocks: [[String: Any]] = (request.results ?? []).compactMap { observation in
            guard let candidate = observation.topCandidates(1).first, candidate.confidence >= 0.35 else { return nil }
            let box = observation.boundingBox
            return ["text": candidate.string, "x": box.minX, "y": 1 - box.maxY,
                    "width": box.width, "height": box.height, "confidence": candidate.confidence]
        }
        output.append(blocks)
    }
    let data = try JSONSerialization.data(withJSONObject: output)
    FileHandle.standardOutput.write(data)
} catch {
    FileHandle.standardError.write(Data("OCR failed: \(error)".utf8))
    exit(1)
}
