import warnings
warnings.filterwarnings("ignore")
import argparse
import textwrap
import os
import sys
from llm_model_selection import local_LLM
from langchain_neo4j import Neo4jGraph
from reasoning_mode_kg import langchain_reasoning
import json

GREEN = '\033[92m'
YELLOW = '\033[93m'
RED = '\033[91m'
RESET = '\033[0m'

def testing_neo4j_mode():
    """
    This is just a temporary placeholder until there is a permanent database
    to call from.
    """
    # These will be stored in a config.
    os.environ["NEO4J_URI"] = "bolt://localhost:7687"
    os.environ["NEO4J_USERNAME"] = "anything"
    os.environ["NEO4J_PASSWORD"] = "anything"

    graph = Neo4jGraph()
    movies_query = """
    LOAD CSV WITH HEADERS FROM 
    'https://raw.githubusercontent.com/tomasonjo/blog-datasets/main/movies/movies_small.csv'
    AS row
    MERGE (m:Movie {id:row.movieId})
    SET m.released = date(row.released),
        m.title = row.title,
        m.imdbRating = toFloat(row.imdbRating)
    FOREACH (director in split(row.director, '|') | 
        MERGE (p:Person {name:trim(director)})
        MERGE (p)-[:DIRECTED]->(m))
    FOREACH (actor in split(row.actors, '|') | 
        MERGE (p:Person {name:trim(actor)})
        MERGE (p)-[:ACTED_IN]->(m))
    FOREACH (genre in split(row.genres, '|') | 
        MERGE (g:Genre {name:trim(genre)})
        MERGE (m)-[:IN_GENRE]->(g))
    """

    graph.query(movies_query)

    return graph


def ask_user_question(question_to_user, exit_mode):
    answer = input(question_to_user).lower()
    if answer in ['y', 'yes']:
        return True
    elif exit_mode is True:
        print(f"{RED}Question cancelled...{RESET}")
        sys.exit(0)
    else:
        return False


tool_description = textwrap.dedent('''
-------------------------------------------                                   
KNOWLEDGE GRAPH LLM Query
-------------------------------------------                                   
This tool allows analysts to ask questions of graphical databases in plain text and
recieve a structured query, results and summarization using an LLM.
                                   ''')


def main():
    parser = argparse.ArgumentParser(description= tool_description,formatter_class=argparse.RawDescriptionHelpFormatter)
    # parser.add_argument("mode", choices=["networkx"], help="Select the graph database to use.")
    parser.add_argument("--question", "-q", type= str, help= "Type the question you want to ask (Wrap in "" for now but will chnage in front end).")
    # parser.add_argument("--graph", "-g", type= str, help= "Add the graph data you want to inspect.")

    args = parser.parse_args()

    print(args.question)


    print(f"{YELLOW}Starting process...{RESET}")

    # Initialise the LLM
    print(f"Initialising AI model and graph.")
    llm = local_LLM()

    print(f"{GREEN}AI successfully loaded.{RESET}")


    # Temporary neo4j loading (replace with actual approach).
    neo4j_graph = testing_neo4j_mode()
    print(f"{GREEN}Graph successfully loaded.{RESET}")

    # Intialise the class
    graph_querying = langchain_reasoning(
        graph = neo4j_graph,
        llm = llm,
        question = args.question,
        graph_type= "neo4j", # just place holder until more known about the process.
        display_names= ["title", "name"] # Adding verbose label assignment option. (for multiple identifiers)
    )

    graph_querying.langchain_neo4j_processor()

    graph_querying.select_example_prompts()

    if len(graph_querying.example_prompts) == 0:
        print(f"{RED} WARNING: No previous prompt examples successfully loaded.{RESET}")
        ask_user_question(question_to_user= "Do you want to continue? [y/N]", exit_mode= True)
        print("Continuing with process...")
    else:
        print(f"{GREEN} {len(graph_querying.example_prompts)}: prompts loaded to support AI generation.{RESET}")

    graph_querying.nlp_pipeline()

    print("Checking the question against graph.")

    adapted_question, successful_matches = graph_querying.entity_checking_parser()

    if len(successful_matches) == 0:
        print(f"{RED}WARNING: no entities detected from question in graph.{RESET}")
        ask_user_question(question_to_user= "Do you want to continue? [y/N]", exit_mode= False)
        print("Continuing with process...")
    elif adapted_question == args.question:
        print(f"{GREEN}The following entities have been found: {successful_matches}. Parsing to LLM. {RESET}")
    else:
        print(f"{YELLOW}Entities found close to existing node: Did you mean {adapted_question}?{RESET}")
        answer = ask_user_question(question_to_user= "Do you want to use this adapted question instead? [y/N]", exit_mode= False)
        if answer is True:
            graph_querying.question = adapted_question
        else:
            pass

    # Check whether we have a good example for the LLM to build from.
    response =None
    example_prompts, scores = graph_querying.prompt_selector_parser()
    if not any(score < 1 for score in scores): # this is the prompt threshold selector. Will be good to figure out a good score for this based on data.
        print(f"{YELLOW}WARNING: no previous prompts were close to your question."
              f" This can increase the risk of AI hallucinations.{RESET}")
        answer = ask_user_question(question_to_user= "Do you want to continue with prompt querying anyway? [y/N]", exit_mode= False)
        if answer is True:
            response = graph_querying.prompt_reasoning_parser()
            print(response)
        else:
            print("Selecting alternative method of querying.")
            strict_response, df = graph_querying.strict_based_parser()
            print(strict_response)
            # df still needs to be filtered to work better with the LLM.
            # Would also make sense to provide option to return data.
    else:
        response = graph_querying.prompt_reasoning_parser()
        print(response)

    # Add an option to support data or visualise here

    # Option user prompt to a list to help train the LLM.
    if response is not None:
        answer = ask_user_question(question_to_user= "Would you like to save this query to help train the model? [y/N]", exit_mode= False)
        if answer is True:
            saved_answer ={
                    "question": response["query"],
                    "query": response["intermediate_steps"][0]["query"].replace("{", "{{").replace("}", "}}")
                }
            print(json.dumps(saved_answer, indent=2))
            user_prompts = "prompts_store/user_created_prompts.json"
            if os.path.exists(user_prompts):
                with open(user_prompts, 'r') as f:
                    current_prompts = json.load(f)
            else:
                current_prompts = []
            current_prompts.append(saved_answer)
            with open(user_prompts, 'w') as f:
                json.dump(current_prompts, f, indent=2)
            print(f"{GREEN}Query saved.{RESET}")

if __name__ == "__main__":
    main()    