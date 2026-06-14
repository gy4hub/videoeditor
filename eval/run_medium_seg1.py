import time, json, sys
from faster_whisper import WhisperModel

seg_num = int(sys.argv[1])
audio = f'/sessions/amazing-confident-bardeen/mnt/videoeditor/eval/raw_audio_seg{seg_num}.wav'
out = f'/sessions/amazing-confident-bardeen/mnt/videoeditor/eval/medium_seg{seg_num}.json'

print(f'Loading medium model for seg{seg_num}...', flush=True)
model = WhisperModel('Systran/faster-whisper-medium', device='cpu', compute_type='int8')
print('Model loaded', flush=True)

start = time.time()
segs_iter, info = model.transcribe(audio, language='zh', word_timestamps=True, vad_filter=True)

words_data = []
seg_list = []
for seg in segs_iter:
    seg_list.append({'start': round(seg.start, 3), 'end': round(seg.end, 3), 'text': seg.text.strip()})
    if seg.words:
        for w in seg.words:
            words_data.append({'word': w.word, 'start': round(w.start,3), 'end': round(w.end,3), 'confidence': round(w.probability,4)})

elapsed = round(time.time() - start, 2)
result = {
    'source': f'raw_audio_seg{seg_num}.wav',
    'model': 'Systran/faster-whisper-medium',
    'seg_num': seg_num,
    'language': info.language,
    'language_probability': round(info.language_probability, 4),
    'transcribe_time_s': elapsed,
    'words': words_data,
    'segments': seg_list,
}
with open(out, 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print(f'Saved {out}, words={len(words_data)}, elapsed={elapsed}s', flush=True)
text = ''.join(w['word'] for w in words_data)
for kw in ['牛初乳', '私域', '添爸', '带货']:
    print(f'  {kw}: {"FOUND" if kw in text else "NOT FOUND"}')
