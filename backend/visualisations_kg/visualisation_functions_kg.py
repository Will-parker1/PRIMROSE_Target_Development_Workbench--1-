from pyvis.network import Network
import networkx as nx

def basic_pyviz(graph, output="knowledge_graph.html", head_node=None, radius=5):
    """Write an interactive graph, optionally limited to a node neighbourhood."""
    display_graph = graph
    if head_node is not None:
        if head_node not in graph:
            raise KeyError(f"Unknown head node: {head_node}")
        lengths = nx.single_source_shortest_path_length(
            graph.to_undirected(), head_node, cutoff=max(0, int(radius))
        )
        display_graph = graph.subgraph(lengths).copy()

    net = Network(
        height="800px",
        width="100%",
        bgcolor="#222222",
        font_color="white",
        select_menu=True,
        directed=display_graph.is_directed(),
    )
    net.from_nx(display_graph)
    net.show_buttons(filter_=["physics"])
    html_content = net.generate_html()
    with open(output, "w", encoding="utf-8") as out_file:
        out_file.write(html_content)
    print(f"Success! Open '{output}' in your web browser.")
    return output
