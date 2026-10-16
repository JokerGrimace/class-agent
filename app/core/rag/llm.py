import os


class LLM:
    def __init__(self, llm_model, llm_model_name, temperature=0):
        if llm_model == "api_key":
            self.llm = self._build_api_llm(llm_model_name, temperature)
        else:
            self.llm = self._build_local_llm(llm_model_name, temperature)

    def _build_api_llm(self, llm_model_name, temperature):
        from app.core.llm.factory import create_llm_adapter

        # RAG 问题重写复用项目 Adapter，模型与 API Key 均由当前教师凭证决定。
        return create_llm_adapter()

    def _build_local_llm(self, llm_model_name, temperature):
        try:
            from langchain_ollama import ChatOllama
        except ImportError as exc:
            raise ImportError(
                "Local model mode requires langchain_ollama. "
                "Install it or switch llm_model to api_key."
            ) from exc

        # 本地模型地址由部署环境决定，默认仅连接当前机器上的 Ollama。
        return ChatOllama(
            base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
            model=llm_model_name,
            temperature=temperature,
        )
