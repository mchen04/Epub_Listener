"""Backend identifiers shared without depending on CLI configuration."""

from typing import Literal, get_args

TTSEngine = Literal["edge", "kokoro", "kokoro-mlx", "huggingface", "command"]
TTS_ENGINE_CHOICES: tuple[str, ...] = get_args(TTSEngine)
ModelDType = Literal["auto", "float32", "float16", "bfloat16"]
COMMAND_OUTPUT_FORMATS = ("wav", "mp3", "flac", "ogg")
