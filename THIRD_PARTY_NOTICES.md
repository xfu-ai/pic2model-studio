# Third-party notices

## Google model-viewer

The desktop UI and offline browser preview bundle `@google/model-viewer` 4.3.1
from its official npm package. The package is licensed under Apache-2.0, and
its generated distribution retains the license notices for its bundled
dependencies. The package license is included at
`desktop/src-tauri/resources/model-viewer/LICENSE.txt`.

## Pi

This project derives behavior contracts from the Pi agent framework, frozen at
commit `8eef62ed3ea62d646a7fad92fa583fc8d71fec17` (2026-07-24). Pi is
copyright © 2025 Mario Zechner and is licensed under the MIT License.

The Python implementation is an independent, Python-native implementation. It
does not copy Pi's coding-agent CLI, TUI, session-tree implementation, or
TypeScript build and publishing system. The referenced source paths and their
target behavior are recorded in
[`docs/python-pi-agent-source-manifest.md`](docs/python-pi-agent-source-manifest.md).

### MIT License

```text
MIT License

Copyright (c) 2025 Mario Zechner

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Real-ESRGAN ONNX model

The bundled `realesrgan-x4.onnx` model is redistributed from
`AXERA-TECH/Real-ESRGAN` at frozen revision
`45767e2bceb3e624477af4922d31418a7a044bc5`. The model card declares
`BSD-3-Clause-Clear`. The complete license text is bundled beside the model in
`src/aipic_to_model/resources/image_processing/models/`.

## ONNX Runtime

The native ONNX Runtime 1.28.0 dependency and bundled ONNX Runtime Web 1.21.0
WASM/module-loader artifacts are copyright Microsoft Corporation and
contributors and licensed under the MIT License. The complete license text is bundled in
`src/aipic_to_model/resources/image_processing/onnxruntime-web/1.21.0/`.
