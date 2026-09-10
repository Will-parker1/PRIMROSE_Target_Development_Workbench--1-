from pyvis.network import Network

def basic_pyviz(graph, output, head_node, radius = 5):
        
        if head_node != None:
              graph
        net = Network(height="800px", width="100%", bgcolor="#222222", font_color="white", select_menu=True)
        net.from_nx(graph)
        net.show_buttons(filter_=['physics'])
        html_content = net.generate_html()
        with open("knowledge_graph.html", "w", encoding="utf-8") as out_file:
            out_file.write(html_content)
        print(f"Success! Open '{output}' in your web browser.")