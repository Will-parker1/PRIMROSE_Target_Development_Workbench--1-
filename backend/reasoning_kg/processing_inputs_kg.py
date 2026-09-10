import json
import networkx as nx
from langchain_community.graphs.networkx_graph import NetworkxEntityGraph
import os
from pathlib import Path
import spacy

# class graph_load():
#     def __init__(self, file_path):
#         self.file_path = file_path
#         self.graph = None

#     def load_json_kg(self):
#         with open(self.file_path, "r") as f:
#             data = json.load(f)
#             self.graph = nx.node_link_graph(data)


#     def load_neo4j_kg(self):

#         pass

class langchain_parser():
    def __init__(self, graph):
        self.graph = graph
        self.processed_graph = None
        self.nodes = None
        self.nlp = None

    def nlp_pipeline(self):
        if self.nodes is None:
            raise RuntimeError("Process a graph before initialising the NLP pipeline.")
        bundled_model = (
            Path(__file__).resolve().parent
            / "en_core_web_sm-3.7.1"
            / "en_core_web_sm"
            / "en_core_web_sm-3.7.1"
        )
        try:
            nlp = spacy.load(str(bundled_model) if bundled_model.exists() else "en_core_web_sm")
        except OSError as exc:
            raise RuntimeError(
                "The spaCy English model is unavailable. Install en_core_web_sm or "
                "restore the bundled reasoning_kg model directory."
            ) from exc
        ruler = nlp.add_pipe(
            "entity_ruler", 
            before="ner", 
            config={"phrase_matcher_attr": "LOWER"}
        )
        patterns = [{"label": "NODE", "pattern": str(node)} for node in self.nodes]
        ruler.add_patterns(patterns)
        self.nlp = nlp

    def langchain_networkx_processor(self):
        if not isinstance(self.graph, dict):
            raise TypeError("NetworkX input must be node-link JSON loaded as a dictionary.")
        try:
            G = nx.node_link_graph(self.graph, edges="links")
        except TypeError:  # NetworkX < 3.4
            G = nx.node_link_graph(self.graph)
        self.nodes = list(G.nodes)
        self.processed_graph = NetworkxEntityGraph(G)

    def langchain_neo4j_processor(self):

        # Assumming the following env environment set.
        # NEO4J_URI=bolt://localhost:7687
        # NEO4J_USERNAME=neo4j
        # NEO4J_PASSWORD=your_secure_password

        if not os.getenv("NEO4J_URI"):
            raise ValueError("NEO4J_URI environment variable is not set.")
        try:
            self.graph.refresh_schema()
            
        except Exception as e:
            print(f"Failed to connect to Neo4j: {e}")
            raise

        node_query = """
        MATCH (n)
        RETURN labels(n)[0] AS node_type, COALESCE(n.title, n.name) AS node_name
        """

        results = self.graph.query(node_query)
        self.nodes = [record["node_name"] for record in results if record["node_name"] is not None]
        self.processed_graph = self.graph

        return self.processed_graph
