# vllm serve Qwen/Qwen2.5-14B-Instruct --host 0.0.0.0 --port 8000 --trust-remote-code
export HF_HOME=/ephemeral/huggingface_cache
 vllm serve Qwen/Qwen3-Coder-30B-A3B-Instruct --host 0.0.0.0 --port 8080 --max_model_len 131072
#vllm serve Qwen/Qwen3-30B-A3B-Thinking-2507 --host 0.0.0.0 --port 8080 --max_model_len 131072