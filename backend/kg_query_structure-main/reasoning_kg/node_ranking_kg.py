import networkx as nx
import random

def unique_node_hop_count(graph, subset: list[str], hops: int =2):
    """
    Iterates through a subset of the graph and counts
    the total number of unique nodes contributed within a set
    using k hops.
    By running multiple permutations this returns a game
    theory approach to quantifying the contribution and importance
    of each node. The score of which is used for ranking.
    
    This is just a simple algorithm but can easily be adapted or replaced
    with some development recommendations being:
    1. Track convergence
    2. Use edge/node weightings instead of counts for smarter assessment of contribution.
    3. Parallise or rewrite this in Rust or C++ because the loop is slow.

    """
    if not subset:
        return 0
        
    node_set = set()
    for node in subset:
        nodes_reached = nx.single_source_shortest_path_length(graph, node, cutoff=hops)
        node_set.update(nodes_reached.keys())
        
    return len(node_set)

def game_theory_node_ranking(graph, nodes: list[str], iterations: int =3):
    """
    FILL IN/
    """

    node_importance_scores = {node: 0.0 for node in nodes}
    # Currently this is only working with JSON graphs.
    nx_graph = nx.node_link_graph(graph, edges="links")
    
    for _ in range(iterations):
        random.shuffle(nodes)
        current_coalition = set()
        current_value = 0

        for node in nodes:
            current_coalition.add(node)
            new_value = unique_node_hop_count(nx_graph, current_coalition)
            marginal_contribution = new_value - current_value
            node_importance_scores[node] += marginal_contribution
            
            current_value = new_value
        print(f"iteration: {_} complete")
    for node in nodes:
        node_importance_scores[node] /= iterations
        
    return node_importance_scores