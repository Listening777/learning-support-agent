#!/bin/bash
cd /workspace/learning-support-agent/src
exec python3 -m uvicorn learning_support.main:app --host 0.0.0.0 --port ${PORT:-8000}
