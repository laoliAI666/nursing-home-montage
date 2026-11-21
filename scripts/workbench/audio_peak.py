"""Repair just the mixed audio of an unpublished workbench render."""
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys


def run(*args):
    prefix = ['rtk', 'proxy'] if shutil.which('rtk') else []
    return subprocess.run(prefix + list(map(str, args)), check=True, capture_output=True, text=True)


def levels(video):
    result = run('ffmpeg', '-nostdin', '-hide_banner', '-i', video, '-af',
                 'loudnorm=I=-17:TP=-1.5:LRA=11:print_format=json', '-vn', '-f', 'null', '-')
    report = result.stderr
    return json.loads(report[report.rfind('{'):report.rfind('}') + 1])


def timing(video):
    result = run('ffprobe', '-v', 'error', '-show_entries',
                 'format=duration:stream=codec_type,codec_name,start_time,duration,nb_frames',
                 '-of', 'json', video)
    return json.loads(result.stdout)


def video_hash(video):
    return run('ffmpeg', '-nostdin', '-v', 'error', '-i', video, '-map', '0:v:0',
               '-c:v', 'copy', '-f', 'streamhash', '-hash', 'sha256', '-').stdout


def repair(job):
    video = job/'renders/final.mp4'
    if not video.is_file() or (job/'final.mp4').exists():
        raise ValueError('音峰修正需要尚未交付的渲染文件')
    journal = job/'audio-peak-repair.json'
    before = levels(video)
    peak = float(before['input_tp'])
    if peak <= -1:
        return json.loads(journal.read_text()) if journal.is_file() else {'changed': False}
    if not math.isfinite(peak):
        raise ValueError('无法测出有效的音频峰值')
    original_timing = timing(video)
    original_hash = video_hash(video)
    gain = -2.0 - peak
    old_video, old_audio = original_timing['streams']
    # AAC decoding supplies encoder padding beyond the MP4 edit-list boundary.
    # Keep the original audio track length before encoding again.
    audio_filter = f"volume={gain:.3f}dB,atrim=duration={float(old_audio['duration']):.6f}"
    candidate = video.with_name('final-audio-peak.tmp.mp4')
    try:
        run('ffmpeg', '-y', '-nostdin', '-v', 'error', '-i', video, '-map', '0:v:0', '-map', '0:a:0',
            '-c:v', 'copy', '-af', audio_filter, '-c:a', 'aac', '-b:a', '192k',
            '-movflags', '+faststart', candidate)
        after = levels(candidate)
        adjusted_timing = timing(candidate)
        if float(after['input_tp']) > -1:
            raise ValueError('混音修正后峰值仍未达标')
        new_video, new_audio = adjusted_timing['streams']
        if (old_video != new_video or video_hash(candidate) != original_hash
                or abs(float(adjusted_timing['format']['duration']) - float(original_timing['format']['duration'])) > .01
                or abs(float(new_audio['start_time']) - float(old_audio['start_time'])) > 1/30
                or abs(float(new_audio['duration']) - float(old_audio['duration'])) > 1/30):
            raise ValueError('音峰调整改变了画面或音轨时间，原渲染已保留')
        result = {'changed': True, 'gainDb': round(gain, 3), 'beforeDbtp': peak,
                  'afterDbtp': float(after['input_tp']), 'videoStreamCopied': True}
        candidate.replace(video)
        temporary = journal.with_suffix('.tmp')
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        temporary.replace(journal)
        return result
    finally:
        candidate.unlink(missing_ok=True)


if __name__ == '__main__':
    try:
        print(json.dumps(repair(Path(sys.argv[1])), ensure_ascii=False))
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({'error': f'重新测量或修正混音失败：{exc}'}, ensure_ascii=False))
        sys.exit(1)
