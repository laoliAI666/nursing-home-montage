# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = ["volcengine==1.0.229", "tos==2.9.3", "faster-whisper==1.2.1", "pypinyin==0.55.0", "numpy>=1.26,<3"]
# ///
"""Text -> verified cloned speech -> resumable OmniHuman video, scoped to this project."""
import argparse
import copy
import hashlib
import json
import shutil
import sys
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from avatar_client import AvatarClient
from common import PROJECT_ROOT, SKILL_DIR, audio_duration, command, config, merge_rules, now, read_json, sanitized, save_state, write_json
from tts_client import speech_key, synthesize
from verify import check_audio, finalize


def emit(value):
    print(json.dumps(sanitized(value), ensure_ascii=False, indent=2), flush=True)


def ensure_audio(directory, state, imported=None):
    cfg = state["config"]
    if not state.get("tts"):
        if imported:
            target = directory / "narration-0.mp3"
            command(["ffmpeg", "-y", "-v", "error", "-i", Path(imported).resolve(), "-vn", "-ac", "1",
                     "-ar", cfg["tts"]["sample_rate"], "-c:a", "libmp3lame", "-b:a", "160k", target])
            state["tts"] = {"file": str(target), "reused_audio": str(Path(imported).resolve()), "revision": 0}
        else:
            state["tts"] = synthesize(state["text"], cfg, directory, state.get("revisions", 0))
        state.update(phase="audio_created", qa_status="pending")
        save_state(directory, state)
    current_hash = hashlib.sha256(Path(state["tts"]["file"]).read_bytes()).hexdigest()
    if state.get("qa_status") in ("passed", "reviewed") and state.get("qa_audio_sha256") != current_hash:
        state["qa_status"] = "pending"
        for key in ("submitted_audio", "submitted_duration", "submitted_audio_sha256", "qa_review"):
            state.pop(key, None)
    if state.get("qa_status") not in ("passed", "reviewed"):
        result = check_audio(state["tts"]["file"], state["text"], cfg["verification"], directory)
        state["qa_status"] = "passed" if result["passed"] else "needs_review"
        state["qa_audio_sha256"] = result["sha256"]
        state["phase"] = "audio_checked"
        save_state(directory, state)
        if not result["passed"]:
            emit({"status": "audio_needs_review", "run_dir": str(directory), "check": result,
                  "next": "修正发音后 revise；实际完成听审/独立核验后才可用 review 记录依据。"})
            return False
    if state.get("submitted_audio"):
        return True
    duration = audio_duration(state["tts"]["file"])
    target = state.get("target_seconds")
    if target is not None:
        if duration > target + 0.001 or target - duration > cfg["verification"]["max_tail_padding_seconds"]:
            state["phase"] = "duration_conflict"
            save_state(directory, state)
            emit({"status": "duration_conflict", "actual_seconds": duration, "target_seconds": target,
                  "next": "通过 revise 调整 TTS 语速，或在用户允许时修改文案；保持完整发言。"})
            return False
    submitted = directory / "narration.mp3"
    args = ["ffmpeg", "-y", "-v", "error", "-i", state["tts"]["file"]]
    if target is not None:
        args += ["-af", f"apad=whole_dur={target}", "-t", target]
    args += ["-vn", "-ar", "44100", "-ac", "1", "-c:a", "libmp3lame", "-b:a", "160k", submitted]
    command(args)
    actual = audio_duration(submitted)
    if not 0 < actual < 300 or submitted.stat().st_size >= 100_000_000:
        raise RuntimeError("提交音频须小于300秒且小于100MB；先按完整句规划分段。")
    state.update(submitted_audio=str(submitted), submitted_duration=actual, phase="audio_ready",
                 submitted_audio_sha256=hashlib.sha256(submitted.read_bytes()).hexdigest())
    state["audio_processing"] = {"tts_decoded_seconds": duration, "tail_padding_seconds": max(0, actual - duration),
                                 "post_tts_speed_changed": False, "music_added": False}
    save_state(directory, state)
    return True


def continue_run(directory, state, wait_seconds, audio_only=False, imported=None):
    if state["phase"] == "complete":
        if Path(state["verification"]["output"]).is_file():
            emit({"status": "complete", "output": state["verification"]["output"], "run_dir": str(directory)})
            return 0
        if (directory / "provider-video.mp4").is_file():
            state["phase"] = "downloaded"
        else:
            raise RuntimeError("已完成记录的成片和原片均缺失；核实文件位置后恢复，不会自动重提。")
    if state["phase"] in ("submitting", "submission_unknown", "failed", "submit_rejected"):
        raise RuntimeError("此记录需要先核查失败/提交状态；不会自动重复提交。")
    if not state.get("task_id"):
        if not ensure_audio(directory, state, imported):
            return 3
        if audio_only:
            emit({"status": "audio_ready", "audio": state["submitted_audio"], "run_dir": str(directory)})
            return 0
        AvatarClient(state["config"]).submit(directory, state)
    if state["phase"] != "downloaded":
        if not AvatarClient(state["config"]).poll(directory, state, wait_seconds):
            emit({"status": "pending", "run_dir": str(directory), "task_id": state["task_id"],
                  "next": "使用 resume 继续查询；不要重新 generate。"})
            return 2
    state["verification"] = finalize(directory, state)
    state["phase"] = "complete"
    save_state(directory, state)
    with zipfile.ZipFile(directory / "narration-package.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ("narration.mp3", "text.txt", "audio-check.json", "run.json", "verification.json"):
            archive.write(directory / name, arcname=name)
    emit({"status": "complete", "run_dir": str(directory), "verification": state["verification"]})
    return 0


def create(args):
    cfg = copy.deepcopy(config())
    if args.speech_rate is not None:
        cfg["tts"]["speech_rate"] = args.speech_rate
    if args.image:
        cfg['image']=str(Path(args.image).resolve())
    if args.prompt_file:
        cfg["avatar"]["prompt"] = Path(args.prompt_file).read_text(encoding='utf-8').strip()
    if args.rules_file:
        cfg["tts"]["pronunciation_dict"] = merge_rules(cfg["tts"]["pronunciation_dict"], read_json(args.rules_file))
    text = (args.text if args.text is not None else Path(args.text_file).read_text(encoding='utf-8')).strip()
    if not text:
        raise RuntimeError("输入文本不能为空。")
    directory = (Path(args.out).resolve() if args.out else PROJECT_ROOT / cfg["output_dir"] /
                 (datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]))
    if directory.exists() and any(directory.iterdir()):
        raise RuntimeError("输出目录已有文件；继续已有任务请用 resume，变更版本请用新目录。")
    directory.mkdir(parents=True, exist_ok=True)
    image = SKILL_DIR / cfg["image"]
    if not image.is_file() or image.stat().st_size >= 4_700_000:
        raise RuntimeError("默认人物图缺失或超过Base64上传限制。")
    shutil.copy2(image, directory / "portrait.png")
    (directory / "text.txt").write_text(text + "\n", encoding='utf-8')
    (directory / "prompt.txt").write_text(cfg["avatar"]["prompt"] + "\n", encoding='utf-8')
    state = {"version": 1, "created_at": now(), "phase": "prepared", "qa_status": "pending", "text": text,
             "image": str(directory / "portrait.png"), "image_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
             "config": cfg, "target_seconds": args.target_seconds, "revisions": 0}
    save_state(directory, state)
    emit({"status": "prepared", "run_dir": str(directory)})
    return continue_run(directory, state, args.wait_seconds, args.audio_only, args.reuse_audio)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    generate = commands.add_parser("generate", help="从文本生成视频；配音检查有疑点时保留检查点")
    source = generate.add_mutually_exclusive_group(required=True)
    source.add_argument("--text")
    source.add_argument("--text-file")
    generate.add_argument("--out")
    generate.add_argument("--image",help="Override the bundled portrait with a local image")
    generate.add_argument("--speech-rate", type=int, choices=range(-50, 101), metavar="[-50,100]")
    generate.add_argument("--target-seconds", type=float)
    generate.add_argument("--prompt-file")
    generate.add_argument("--rules-file", help='JSON object: {"tone":["生活/(sheng1)(huo2)"]}')
    generate.add_argument("--reuse-audio", help="仅复用已存在且对应同一文本的音频，仍执行内容核对")
    generate.add_argument("--audio-only", action="store_true")
    generate.add_argument("--wait-seconds", type=int, default=45)
    resume = commands.add_parser("resume")
    resume.add_argument("run_dir")
    resume.add_argument("--wait-seconds", type=int, default=45)
    resume.add_argument("--audio-only", action="store_true")
    revise = commands.add_parser("revise", help="提交视频前修正配音；默认最多两轮")
    revise.add_argument("run_dir")
    revise.add_argument("--rules-file")
    revise.add_argument("--speech-rate", type=int, choices=range(-50, 101), metavar="[-50,100]")
    revise.add_argument("--text-file", help="仅在用户允许修改文案时提供")
    review = commands.add_parser("review", help="记录实际完成的听审或独立核验；不提交视频")
    review.add_argument("run_dir")
    review.add_argument("--note", required=True)
    review.add_argument("--evidence", required=True, help="实际听审/第二路核验的记录文件")
    doctor = commands.add_parser("doctor", help="检查资产、依赖、凭据；可只读查询已知视频任务")
    doctor.add_argument("--task-id")
    cleanup = commands.add_parser("cleanup")
    cleanup.add_argument("run_dir")
    args = parser.parse_args()
    if args.action == "generate":
        if args.target_seconds is not None and not 0 < args.target_seconds < 300:
            raise RuntimeError("目标时长须大于0且小于300秒。")
        return create(args)
    if args.action == "doctor":
        cfg = config()
        speech_key(cfg["tts"])
        client = AvatarClient(cfg)
        result = {"default_portrait_exists": (SKILL_DIR / cfg["image"]).is_file(),
                  "ffmpeg": bool(shutil.which("ffmpeg")), "ffprobe": bool(shutil.which("ffprobe")),
                  "tts_credential_found": True, "ak_sk_found": True, "speaker": cfg["tts"]["speaker"]}
        if args.task_id:
            from common import sanitized
            result["existing_task"] = sanitized(client.query(args.task_id), (client.ak, client.sk))
        emit(result)
        return 0
    directory = Path(args.run_dir).resolve()
    state = read_json(directory / "run.json")
    if args.action == "cleanup":
        if state["phase"] not in ("complete", "downloaded", "failed", "submit_rejected"):
            raise RuntimeError("任务仍可能读取音频；结束或明确失败后再清理临时文件。")
        AvatarClient(state["config"]).cleanup(directory, state)
        emit({"upload": state.get("upload")})
        return 0
    if args.action == "review":
        if state.get("task_id") or state["phase"] in ("submitting", "submission_unknown"):
            raise RuntimeError("任务已提交，不能在此修改配音核验记录。")
        if not (directory / "audio-check.json").is_file():
            raise RuntimeError("先运行配音检查。")
        evidence = Path(args.evidence).resolve()
        if not evidence.is_file() or not args.note.strip():
            raise RuntimeError("需要实际核验记录文件及说明。")
        state["qa_review"] = {"note": args.note, "evidence": str(evidence),
                              "audio_sha256": hashlib.sha256(Path(state["tts"]["file"]).read_bytes()).hexdigest()}
        state["qa_status"] = "reviewed"
        state["qa_audio_sha256"] = state["qa_review"]["audio_sha256"]
        save_state(directory, state)
        emit({"status": "review_recorded", "next": "resume"})
        return 0
    if args.action == "revise":
        if state.get("task_id") or state["phase"] in ("submitting", "submission_unknown"):
            raise RuntimeError("已提交的视频使用旧音频；修改配音须创建新版本。")
        if state["revisions"] >= state["config"]["verification"]["max_tts_revisions"]:
            raise RuntimeError("已达到两轮配音修正上限；报告具体疑点，保留音频。")
        if args.rules_file:
            state["config"]["tts"]["pronunciation_dict"] = merge_rules(
                state["config"]["tts"]["pronunciation_dict"], read_json(args.rules_file))
        if args.speech_rate is not None:
            state["config"]["tts"]["speech_rate"] = args.speech_rate
        if args.text_file:
            state["text"] = Path(args.text_file).read_text(encoding='utf-8').strip()
            (directory / "text.txt").write_text(state["text"] + "\n", encoding='utf-8')
        state["revisions"] += 1
        for key in ("tts", "submitted_audio", "submitted_duration", "submitted_audio_sha256", "qa_review", "qa_audio_sha256"):
            state.pop(key, None)
        state.update(phase="prepared", qa_status="pending")
        save_state(directory, state)
        return continue_run(directory, state, 0, audio_only=True)
    return continue_run(directory, state, args.wait_seconds, args.audio_only)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        emit({"status": "stopped", "error": str(error)})
        sys.exit(1)
