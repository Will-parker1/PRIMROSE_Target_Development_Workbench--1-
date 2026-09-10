from langchain_neo4j import GraphCypherQAChain
from langchain_core.prompts import PromptTemplate
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_core.prompts import FewShotPromptTemplate, PromptTemplate
from langchain_community.vectorstores import FAISS
from langchain_core.example_selectors import SemanticSimilarityExampleSelector

def euclidean_prompt_selector(question: str, example_prompts: dict):
    """
    figure out a sensible threshold.
    """


    hf_embeddings = HuggingFaceEmbeddings(model_name= "all-MiniLM-L6-v2")
    example_selector = SemanticSimilarityExampleSelector.from_examples(
        examples= example_prompts,
        embeddings= hf_embeddings,
        vectorstore_cls= FAISS,
        k=3,
        input_keys=["question"],
    )
    docs_and_scores = example_selector.vectorstore.similarity_search_with_score(
        question, 
        k=example_selector.k
    )
    print(f"--- Top examples for: '{question}' ---")
    for i, (doc, score) in enumerate(docs_and_scores, 1):
        print(f"{i}. Question: {doc.metadata.get('question', 'N/A')}")
        print(f"   Cypher:   {doc.metadata.get('query', 'N/A')}\n")
        print(f"   Score:    {score}\n")

    return example_selector, docs_and_scores

def example_prompt_creator(example_selector):

    example_prompt = PromptTemplate.from_template(
    "User input: {question}\nCypher query: {query}"
)
    prompt = FewShotPromptTemplate(
        example_selector=example_selector,
        example_prompt= example_prompt,
        prefix="You are a Neo4j expert. Given an input question, create a \
            syntactically correct Cypher query to run.\n\nHere is the schema information\n{schema}.\n\nBelow are a" \
            " number of examples of questions and their corresponding Cypher queries.",
        suffix="User input: {question}\nCypher query: ",
        input_variables=["question", "schema"],
    )

    return prompt

def neo4j_prompt_reasoning(graph, llm, question: str, example_prompts: dict, prompt_subset):
    """
    This function takes in a user question and neo4j
    graph and completes the following:
    1. Selects similar prompts to the user question.
    2. Builds a cypher prompt to help the LLM build the query.
    3. Runs the query, validating the cypher including edge direction.
    4. Returns as summary of the results.

    This mode works well when there are good prompt examples.
    However, it can struggle when unfamiliar queries are provided.
    The fail safe is that the query is returned to the user for checking.
    """

    if prompt_subset is None:
        prompt_subset, scores= euclidean_prompt_selector(question, example_prompts)

    selected_cypher_prompts = example_prompt_creator(prompt_subset)

    chain = GraphCypherQAChain.from_llm(
        graph= graph,
        llm= llm,
        cypher_prompt= selected_cypher_prompts,
        verbose=True,
        allow_dangerous_requests=True, # figure out a better fix for this.
        validate_cypher=True,
        return_intermediate_steps=True
    )
    response = chain.invoke({"query": question})

    return response