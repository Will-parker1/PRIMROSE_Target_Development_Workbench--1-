# Overview of the repository:

### Set up instructions:

For this local version of the workflow you will need:
1. An LLM API connection or locally hosted model through LMStudio: https://lmstudio.ai/download
2. Neo4j installed or connected to for storing and querying data. Set up instructions are available here:https://neo4j.com/docs/operations-manual/current/installation/

This workflow was adapted and developed from the following langchain tutorial:
https://github.com/langchain-ai/langchain/blob/72c8b3127dfaa5c68ef0d66cdb934b785bdfaa29/docs/docs/use_cases/graph/quickstart.ipynb

Please note, this is just for locally hosting and running code. The actual set up will be very different.

### building_kg: this is a folder for doing feature extraction and knowledge graph building.

 My recommended process would be:

1. Processing script: import the file types and standardise the output. For example, docx, csv, pdf -> import -> export as txt or pkle files.
2. Cleaning script: reduce the amount of text required to be read by the LLM by reading in these standardised files and removing unwanted text such as headers, classified information etc. This stage is extremely valuable and getting it right will improve the llm performance significantly both in terms of speed and accuracy. (Junk in junk out)
3. Pre-structuring for LLM script: Prior to processing into the LLM, try and standardise chunks of text in each data source. This will significantly reduce the overhead of the LLM having to interpret structures internally. Again, this can help speed up processing and accuracy.
4. Entity and relationship extraction script: use an LLM or an NLP model to extract entities and relationships from the graph using standardised data inputs. Then prior to building the graph, save this off as tabular data.
5. Further feature building script: the analyst or developer may want to add non-AI created entities such as graph centrality scores, loop based feature extraction such as entity counts, document length, document importance etc. Add these to the tabular data.
6. Building graphs: this is the most challenging part of the building the graph and will likely be a combination of fuzzy entity matching, context grabbing, re parsing sections through LLMs and strict schemas. But having the data exported in tabular format will help visually with validating relationships.

### front_end_kg:

Ensure which ever language used to build the front-end has a developer who knows the language. Fixing bugs or speed issues with claude without knowing how the language works is not going to work if there is error propogation. If python is the most common language then start with plotly dash and ensure you are building the front end to match the back, not the back to match the front. The back is the brain of the project. The front-end just demonstrates the process in a clear and readable way.

### Reasoning_kg:

I have written a basic class structure for parsing knowledge graphs to extract data. The primary aim here is to extract graph data as safely and accurately as possible. There is no shame in not using AI wherever possible. The less AI you use, the more stable the process will be because AI systems are designed to be stocastic and are very much black-boxes in terms of reasoning.

Below is a brief description of the content of this repository:

llm_model_selection.py: the primary LLM tool being used in langChain. This file stores the processing steps to connect to a LLM in a format recognised by langchain. I think most of the big AI API techniques are enabled but do check this. Do not include API keys in the real product but store them as secure environment variables.

node_ranking_kg.py: this is where I have been storing the node ranking data which assigns scores to each node in the graph. I have just included a basic monte carlo game theory loop which counts the edge of nodes in a subgraph and counts the unique contribution of each node to a set of edges. There are many techniques to node ranking so definitely have fun and explore these techniques. Because this is "feature extraction", ensure these scores are updated both in the underlying tabular data and graph created during the feature extraction stage and the graph.

processing_inputs.py: this stores a class called initialise_graph_for_langchain(). The primary function is to develop different graph inputs whether this is for networkx, json, neo4j or another format and store all the required information such as processed_graph, nodes etc. It acts as the base class for the reasoning. If there is any object which will be used repeatidly in the reasoning stage, complete this stage here. That way should something fail it is easy to track which initialisation function is not working as it will point to the class function. The NLP model requires the node information to be added to the model dictionary for entity detection. This is an example of an initialisation stage, which was originally a subprocess in a larger function later in the process which can be stored from the beginning initialisation. You don't know when future developers are also going to want to use these initialised nlp models in later stages.

reasoning_mode_kg.py: this is the front door of the graphRAG system. When you initialise this class it should automatically inherit the functions from the base class (initialise_graph_for_langchain). It then contains different reasoning modes and the intention is you can develop different if statements for the inputs controlled at this level. For example, prompt_reasoning_parser needs to call different functions for neo4j compared with networkx because the queries generated are entirely different.

question_entity_checker.py: this has a simple entity checking function which uses entity extraction and fuzzy matching to determine what entities are in the users question. The intention here is to flag a warning to the user if they have mispelled node names or not included anything from the graph. This process is extremely quick which prevents the loading of an LLM which might fail and wasting time. It also helps warn against potential hallucinations from the LLM, which agreeably generates a working query despite the question being unanswerable. This code still needs a bit of work but the intention is be a warning rather than a kill switch.

prompt_reasoning_parser.py:this is the standard approach to using an LLM for a graphRAG. The basic process is as follows:

1. Create a list of example questions and queries to help the LLM to recognise and correctly build queries.
2. Parse a user question to a semantic similarity selector. This uses basic euclidean or cosine distances with a small embedding model (which will need to be locally downloaded and stored) to select and appropriate subset of prompt questions which are relevant to the question. Selecting a subset of the prompts enables the LLM to do far less work in reasoning how to build a query. Additionally, semantic scores can offer some indication of whether the users questions has any relevant previous prompt examples. Again, this is another way of warning the user that the model might fail.
3. Generate a query using the question and a subset of previous examples. This langchain workflow will automatically validate the query runs against the graph and return all the relevant data.

strict_multi_hop_reasoning.py: this was an experimental backup I had to the situation where the prompts are not that relevant to the user question and the user question is about multi-hops from a specific node/nodes. It works by identifying nodes and relationships which are in the user query and parsing this to a hard coded query.

1. The relationships in the graph are called and stored as a list. This is an example of a function which could be initialised in 'processing_inputs_kg.py'. The LLM then selects which relationships from the python list are relevant to the user query. I currently just have this enabled as "pick one" but multi and None should also be enabled.

2. Using the nodes and relationships from the question and grounding a "head node", a hop count is selected. This basically takes a head node and calculates how many node hops to all neighbours would be required to answer this question. This is a very naive way of doing this but there may be some graph theory approach which works better.

3. Say the entities 'Brad Pitt' and directors are a minimum of 3 hops away and the question is Who directed Brad Pitt? You query all nodes 3 hops away from Brad Pitt and return the data as a dataframe. You then discard all the irrelevant information and return the data as a subset. The user can then look through the data subset or AI can summarise. So even if 'Brad Pitt' is connected to a director by 4 hops, but this director did not direct a film Brad Pitt is in, this is visible in the subset of data.

I recognise this is a bit clunky, but it really serves as an experimental backup to your standard prompt template query which may not work on multi-hop queries, especially complex ones. At the very least the analyst can hopefully obtain a relevant subset of the graph to explore their question further.

For questions regarding ranking and graph summarisation. Because these are so standard as queries, I think there is no point allowing an LLM to write the whole query, but rather just get the LLM to recognise the question is a summarisation question and populate a standardised prompt. This could be another reasoning mode entirely.

The idea then is to have a layer above the reasoning_mode_kg.py which orchastrates the selection of reasoning by identifying which mode to use. "prompt" mode, "strict multihop mode", "summary mode" etc. Additionally, if prompt mode is default but the prompt examples look irrelevant based on semantic score, you could switch to another safer mode automatically, without generating an entire query through an LLM.

### Unit tests:

If you are worried that a python object may be the wrong format or a function might output something it is not meant to then unit testing can be a good centralised way to monitor and track this behaviour. If the unit tests fail, you know there is unexpected behvaiour even if the input and output of the workflows look correct. This preventative step can help you avoid unwanted bugs in the future. But is absolutely not a requirement if pressed for time.


Additional considerations:
- Test on messy data. LLM generated data is highly logical and will not represent problematic documents. The aim is to get the hard stuff correct as well. Because as soon as the data types do not match the schemas the model is going to have to do additionally thinking and this needs to be tested early.
- Test on standardised test sets: the reason to use a basic standardised test set initially is that you can compare your processes to thousands of other developers to validate processes.
- If loops become computationally expensive, develop a rust or c++ wrapper to do the bulk of the loop and feed this back into python. Most modern python packages are built with these languages at the base so there is no shame in using a c++ package wherever possible for loops.
- You want each stage of a data science life cycle process to be "packaged" as a step. All the cleaning happens at one stage, all the feature extraction at another, all the modelling in the next. If you have a step further in the process which adds more features, wherever possible code this into the feature building, even if it is steps like encoding or vectorisation. This will not always be possible as exampled with the node ranking, but if this happens, make sure the output is fed backwards to be stored as well as forward.


### Future development:

1. I have not developed the summaries because I think this should be an additional step given the low processing capabilities of Gemma models. It is also dependent on the customer at this point and might require extra steps such as summarising content within nodes.
    For now I have left the output in the following format which can be parsed to another step to summarise or return to the user:

    - Query: The original question from the user.
    - result: summary (if provided, as langchain automatically does. Often these summaries are wrong so do not rely on them.)
    - intermediatesteps: the query run by the model to extract the data.
    - context: the data returned by the model. Useful for building the actual summaries alongside the user.

2. Strict Reasoning modes. I have just developed a graph 'summary' mode and the multi-hop, which I have relabelled as subgraph because this describes the function better.
To add a new mode:
    - Create the new query function under the class in strict_based_reasoning.py.
    - under the class object self.options add the context for the question to select your mode. For example, the question is asking for a ranking/importance of data.
    - In the reasoning_mode_kg.py file add the new function to the look up dictionary labelled.
    - Ensure your inputs and outputs are the same as the other functions.
    - Test your mode works using both the example_workflow, entry point and front end integration.

3. There is a remote call out to a hugging face model in prompt_based_reasoning.py. This will either need to be downloaded or considered in the deployment.



    
    This gives the the