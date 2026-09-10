import networkx as nx
import spacy
from langchain_community.graphs import NetworkxEntityGraph
import json
import warnings
from pathlib import Path

class initialise_graph_for_langchain():
    """
    This is an initialisation class for the graph for the reasoning process.
    These functions process the graph for langchain and extracts features
    which will be used throughout the reasoning.
    Any information which is likely to be used should be initialised and
    stored in this class.

    - graph: graph to use. Either a path to json file or a database label within neo4j.
    - display_names: defining a list of node label types which will be used to query the graph.
    These should be unique to the node and natural language. Such as the graph.document_title or graph.location.
    Basically, whatever the primary label association the user is likely to use. This process should also
    probably be moved to the graph preprocessing at some point.
    - processed_graph: graph adapted for langchain processes.
    - nodes: Extracts a list of nodes.
    """
    def __init__(self, graph, display_names: list[str], example_prompts= None):
        self.graph = graph
        self.display_names = display_names
        self.example_prompts = example_prompts
        self.processed_graph = None
        self.nodes = None
        self.nlp = None

    def langchain_json_networkx_processor(self):
        """
        Takes a JSON graph, converts to networkx and
        processes the graph for use with langchain using
        NetworkxEntityGraph.
        """
        with open(f"{self.graph}", "r") as graph:
            self.graph = json.load(graph)

        nx_graph = nx.node_link_graph(data=self.graph, edges="links")
        self.nodes = list(nx_graph.nodes)
        self.processed_graph = NetworkxEntityGraph(nx_graph)

    def langchain_neo4j_processor(self):
        """"
        This assumes the following .env variables are set:
        NEO4J_URI=bolt://localhost:7687
        NEO4J_USERNAME=neo4j
        NEO4J_PASSWORD=your_secure_password

        Reads from the Neo4j database and returns the nodes
        and the graph.
        """
        
        display_nodes ="""
        MATCH (n)
        WITH n, [key IN $keys WHERE n[key] IS NOT NULL | n[key]] AS matching_labels
        RETURN
            labels(n)[0] AS display_label,
            COALESCE(matching_labels[0], "Unknown Node") AS display_name
        """

        nodes_extracted = self.graph.query(display_nodes, params={"keys": self.display_names})
        self.nodes = [node["display_name"] for node in nodes_extracted if node["display_name"] != 'Unknown Node']
        if len(self.nodes) < len(nodes_extracted):
            missing_nodes = len(nodes_extracted) - len(self.nodes)
            warnings.warn(
                f"{missing_nodes} nodes are missing a valid display_name."
                "this can cause graph information to be invisible to the query."
            )

        self.processed_graph = self.graph

    def nlp_pipeline(self):
        """
        Loads a NLP model for fuzzy matching terms.
        Embeds the node label strings for lookup.
        Could possible just replace this entire method with an LLM instead.
        Initialising here because reused with different queries..
        """
        nlp = spacy.load(r"C:.\en_core_web_sm-3.7.1\en_core_web_sm\en_core_web_sm-3.7.1")
        embed_nodes = nlp.add_pipe(
            "entity_ruler", 
            before="ner", 
            config={"phrase_matcher_attr": "LOWER"}
        )
        patterns = [{"label": "NODE", "pattern": str(node)} for node in self.nodes]
        embed_nodes.add_patterns(patterns)
        self.nlp = nlp

    def select_example_prompts(self):

        folder_path = Path(__file__).parent / 'prompts_store'
        prompt_examples= []
        if self.example_prompts != None:
            with open(self.example_prompts, 'r', encoding= 'utf-8') as file:
                prompts = json.load(file)
                prompt_examples.extend(prompts)

        for prompt_file in folder_path.glob('*json'):
            with open(prompt_file, 'r', encoding= 'utf-8') as file:
                try:
                    prompts = json.load(file)
                    prompt_examples.extend(prompts)
                except json.JSONDecodeError:
                    print(f"Warning {prompt_file} is not valid json and not included in prompt examples")
        self.example_prompts = prompt_examples        
            