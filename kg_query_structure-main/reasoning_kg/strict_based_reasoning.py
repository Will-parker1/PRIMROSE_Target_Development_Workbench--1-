from neo4j import GraphDatabase
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from typing import Any
import pandas as pd
# This is for neo4j, you'll want  separate script for other modes.

summary_modes= [
    "1. question asking for an overall summary.",
    "2. question asking for most, least, importance, irrelevant or something related.",
    "3. question asking for a specific count of a feature."
    "4. question is asking about unknown relationships relative to one entity."
    ]

def mode_selector(question: str, llm, options):

    prompt = PromptTemplate.from_template(
        "Classify the user's question into exactly one of these categories:" \
        "{options}"
        "question: {question}\n\n"
        "Return ONLY the exact category name and nothing else."
    )

    chain = prompt | llm | StrOutputParser()
    final_choice = chain.invoke({
        "options": options, 
        "question": question
    })

    return final_choice


def get_edge_count(head_id: str, edge_id: str):
    cypher_query = f"""
        WITH {head_id} AS search_terms
        MATCH (head)
        WHERE any(k IN keys(head) WHERE k <> 'embedding' AND toString(head[k]) IN search_terms)
        WITH head LIMIT 1
        MATCH (target)
        WHERE (target)-[:{edge_id}]-() AND head <> target
        MATCH p = shortestPath((head)-[*]-(target))
        RETURN length(p) + 1 AS edge_count
        ORDER BY edge_count ASC
        LIMIT 1
        """
    
    driver = GraphDatabase.driver("neo4j://localhost:7687", auth=("neo4j", "password"))
    with driver.session() as session:
        result = session.run(cypher_query)
        record = result.single()
        
    return record

def get_entity_count(question: str, nlp: Any):
    doc = nlp(question)
    test_phrases = [ent.text for ent in doc.ents]
    for chunk in doc.noun_chunks:
        if chunk.text not in test_phrases:
            test_phrases.append(chunk.text)
    if not test_phrases:
        test_phrases = [question[-1]]
    print(test_phrases)
    return len(test_phrases)


class neo4jStrictMode:
    def __init__(self, graph: Any, question: str, entity_matches: list[str], llm: Any, nlp: Any):
        self.graph = graph
        self.question = question
        self.entity_matches = entity_matches
        self.llm = llm
        self.nlp = nlp
        self.options = [
        "1. question asking for an OVERALL summary.",
        "2. question is asking about unknown relationships relative to one entity."
        ]
        self.selected_option = mode_selector(question= self.question, llm= self.llm, options= self.options)
        
    def overall_graph_summary(self):
        cypher_query = """
            CALL db.labels() YIELD label
            RETURN {name: 'labels', data: COLLECT(label)[..1000]} AS result
            UNION ALL
            CALL db.relationshipTypes() YIELD relationshipType
            RETURN {name: 'relationshipTypes', data: COLLECT(relationshipType)[..1000]} AS result
            UNION ALL
            CALL db.propertyKeys() YIELD propertyKey
            RETURN {name: 'propertyKeys', data: COLLECT(propertyKey)[..1000]} AS result
            UNION ALL
            MATCH ()
            RETURN {name: 'nodes', data: count(*)} AS result
            UNION ALL
            MATCH ()-[]->()
            RETURN {name: 'relationships', data: count(*)} AS result
        """
        driver = GraphDatabase.driver("neo4j://localhost:7687", auth=("neo4j", "password"))
        try:
            with driver.session() as session:
                result = session.run(cypher_query)
                summary_data = [record["result"] for record in result]

            response = {
                    'query': self.question,
                    'result': None,
                    'intermediate_steps': [
                        {'query': cypher_query}
                    ],
                    'context': summary_data
                }

            df = pd.DataFrame()
                
            return response, df
        finally:
            driver.close()

    def subgraph_queries(self):
        """
        Need to enable this with multiple relationships and nodes and refactor
        for the different graph types.
        """

        result = self.graph.query("CALL db.relationshipTypes()")
        relationships = [row["relationshipType"] for row in result]
        options_str = ", ".join(relationships)
        filter_relationship = mode_selector(question= self.question, llm= self.llm, options= options_str)
        filter_relationship = str(filter_relationship).strip()
        edge_count = get_edge_count(head_id= self.entity_matches, edge_id= filter_relationship)
        entity_count = get_entity_count(question= self.question, nlp=self.nlp)
        hops = list(edge_count.data().values())
        hops.append(entity_count)
        hops.sort(reverse=True)

        print(hops)
        print(hops[0])
        
        cypher_query = f"""
            WITH {self.entity_matches} AS search_terms
            MATCH (origin)
            WHERE any(k IN keys(origin) WHERE k <> 'embedding' AND toString(origin[k]) IN search_terms)
            OPTIONAL MATCH path = (origin)-[*1..{hops[0]}]-(neighbor)
            UNWIND (CASE WHEN path IS NULL THEN [null] ELSE relationships(path) END) AS rel
            WITH DISTINCT origin, rel
            RETURN 
                origin AS search_node,
                startNode(rel) AS start_node, 
                type(rel) AS relationship, 
                endNode(rel) AS end_node
        """
        # move this to the initialisation.
        driver = GraphDatabase.driver("neo4j://localhost:7687", auth=("neo4j", "password"))
        with driver.session() as session:
            result = session.run(cypher_query)
            print(result)
            df = result.to_df(expand=True, parse_dates=True)

    ######################################################################################
        # Make this a dynamic filtering mode
        filtered_df = df[df['relationship'] == filter_relationship]
    ######################################################################################

        summary_prompt = f"""
            "You are a helpful assistant. Use the following dataset to answer the user's question.\n"
            "DATA SCHEMA:\n"
            "- 'start': The name of the main node from query.\n"
            "- 'relationship': How they are connected.\n"
            "- 'end': The name of the connected nodes.\n\n"
            "DATA:\n{filtered_df}\n\n"
            "Question: {self.question}"
            """
        
        llm_response= self.llm.invoke(summary_prompt).content

        # Transform th output to be the same as the langchain.

        response = {
                'query': self.question,
                'result': llm_response,
                'intermediate_steps': [
                    {'query': cypher_query}
                ],
                'context': df
            }

        return response, df




