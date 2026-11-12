"""Offline synthetic-noise and public-speech regression using local ONNX models."""
from pathlib import Path
import json
import queue
import wave
import numpy as np
from iris_caption.audio import ContextualOnnxAdapter, _recognize
from iris_caption.segmentation import Segmenter
from iris_caption.streaming_vad import StreamingFsmnVad

root = Path(__file__).resolve().parents[3]
cache = Path.home() / '.cache/modelscope/hub/models/iic'
adapter = ContextualOnnxAdapter(cache / 'speech_paraformer-large-contextual_asr_nat-zh-cn-16k-common-vocab8404-onnx',
    vad_model_path=cache / 'speech_fsmn_vad_zh-cn-16k-common-onnx')
with wave.open(str(root / 'output/iris-caption-benchmark/asr_example_zh.wav')) as source:
    assert source.getframerate() == 16000 and source.getsampwidth() == 2
    speech = np.frombuffer(source.readframes(source.getnframes()),dtype='<i2').astype(np.float32)/32768
rng=np.random.default_rng(42)
t=np.arange(64000)/16000
cases={'silence':np.zeros(64000),'white_noise':rng.normal(0,.015,64000),
       'hum60':.02*np.sin(2*np.pi*60*t),'tone1000':.02*np.sin(2*np.pi*1000*t),
       'public_speech':speech,'speech_attenuated_12db':speech*.25}
report={}
for name,audio in cases.items():
    segmenter=Segmenter(StreamingFsmnVad(cache / 'speech_fsmn_vad_zh-cn-16k-common-onnx'))
    events=queue.Queue()
    config={'session_id':name}
    for offset in range(0,len(audio),640):
        _recognize(segmenter.feed(audio[offset:offset+640]),adapter,events,config)
    _recognize(segmenter.flush(),adapter,events,config)
    captured=[]
    while not events.empty():
        captured.append(events.get_nowait())
    texts=[event['text'] for event in captured if event['type']=='final' and event['text']]
    report[name]={'final_text':texts,'nonempty_events':sum(bool(e['text']) for e in captured),'events':len(captured)}
    print(name,report[name],flush=True)
output=root/'apps/iris-caption/output/noise-regression.json'
output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
assert all(report[name]['nonempty_events']==0 for name in ('silence','white_noise','hum60','tone1000'))
assert report['public_speech']['final_text'] and report['speech_attenuated_12db']['final_text']
