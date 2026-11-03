import numpy as np
import onnxruntime as ort

class OnnxTensor(np.ndarray):
	# Lets ONNX outputs stand in for the TF tensors the Keras call sites expect (they call .numpy()).
	def numpy(self):
		return np.asarray(self)

class OnnxKerasModel:
	# Drop-in for a Keras model loaded with load_model(): model(x) returns one tensor, or a list for multi-output models.
	def __init__(self, onnx_path, providers=None):
		if providers is None:
			providers = ['CPUExecutionProvider']
		self.session = ort.InferenceSession(onnx_path, providers=providers)
		self.input_name = self.session.get_inputs()[0].name

	def __call__(self, x):
		outputs = self.session.run(None, {self.input_name: np.asarray(x, dtype=np.float32)})
		outputs = [output.view(OnnxTensor) for output in outputs]
		return outputs[0] if len(outputs) == 1 else outputs
