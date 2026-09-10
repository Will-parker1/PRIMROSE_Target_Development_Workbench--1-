from processing_inputs_kg import initialise_graph_for_langchain
from question_entity_checker import entity_checking
from prompt_based_reasoning import neo4j_prompt_reasoning, euclidean_prompt_selector
from strict_based_reasoning import neo4jStrictMode
from typing import Any

class langchain_reasoning(initialise_graph_for_langchain):
    def __init__(self, graph: Any, llm: Any, question: str, graph_type: str, display_names: list[str], example_prompts=None):
        super().__init__(graph, display_names, example_prompts)
        self.llm = llm
        self.question = question
        self.graph_type = graph_type
        self.response = []
        self.entity_matches = None
        self.prompt_subset = None

    def entity_checking_parser(self):

        adapted_question, matches = entity_checking(
            question= self.question,
            nlp_model=self.nlp,
            nodes= self.nodes)

        self.entity_matches = matches

        return adapted_question, matches

    def prompt_selector_parser(self):

        example_selected, docs_and_scores = euclidean_prompt_selector(
            question = self.question,
            example_prompts= self.example_prompts
        )
        scores = [score for doc, score in docs_and_scores]

        self.prompt_subset = example_selected
        return example_selected, scores


    def prompt_reasoning_parser(self):

        if self.graph_type == "neo4j":
            response = neo4j_prompt_reasoning(
                graph = self.graph,
                llm = self.llm,
                question = self.question,
                example_prompts= self.example_prompts,
                prompt_subset= self.prompt_subset)

        elif self.graph_type == "networkx":
            response = "not enabled."

        return response


    def strict_based_parser(self):

        if self.graph_type == "neo4j":
            strict_mode = neo4jStrictMode(
                graph = self.graph,
                llm = self.llm,
                question = self.question,
                entity_matches= self.entity_matches,
                nlp = self.nlp
            )

            # add more summary modes here based on requirements.
            # For example, Ranking, counts and filters which are very structured queries.
            # Also add a failure mode if the question doesn't fit any.

            summary_mapper ={
                "1": strict_mode.overall_graph_summary,
                "2": strict_mode.subgraph_queries
            }

            option_number_selected = strict_mode.selected_option[0] # this is a bit fragile.
            option_selected = summary_mapper.get(option_number_selected)
            print(f"Option: {option_number_selected} selected for querying.") # Replace this with an actual discription of th emode.
            response, df = option_selected()

            return response, df


        
        
        

        

        
        





