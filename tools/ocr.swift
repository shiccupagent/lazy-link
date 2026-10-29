import Foundation
import Vision
import AppKit
// macOS text recognition (Vision framework), used by frame-scan.sh. usage: swift ocr.swift img1.png img2.png ...  -> "file<TAB>text" per recognised line
for path in CommandLine.arguments.dropFirst() {
    guard let img = NSImage(contentsOfFile: path), let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else { continue }
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    try? VNImageRequestHandler(cgImage: cg).perform([req])
    for o in req.results ?? [] { if let t = o.topCandidates(1).first { print("\(path)\t\(t.string)") } }
}
