import dash

app = dash.Dash()
app.layout = [dash.html.Div(children='Hello World')]

if __name__ == '__main__':
    app.run(debug=True)