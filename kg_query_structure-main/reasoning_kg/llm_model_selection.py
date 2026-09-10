from langchain_openai import ChatOpenAI

def local_LLM():
    """
    Connect to a locally hosted LLM model on a hosting service
    such as LMStudio.
    """
    local_llm = ChatOpenAI(
        base_url="http://localhost:1234/v1",
        api_key="lm-studio",
        temperature = 0 
    )
    try:
        local_llm.invoke("Ping")
    except Exception as e:
        raise ConnectionError(f"Failed to load AI model")
    return local_llm

def select_model(): # automatic mode or manual
    pass