#!/usr/bin/env sh
set -eu
python -m transformer_from_scratch.train --device cpu --epochs 30 --batch-size 16 --seed 1337
python -m transformer_from_scratch.generate --checkpoint outputs/best.pt --prompt "The small machine" --seed 1337
