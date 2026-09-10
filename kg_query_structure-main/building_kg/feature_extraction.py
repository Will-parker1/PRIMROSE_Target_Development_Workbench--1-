from feature_functions import FEATURE_MAP

def process_features(input, config: dict):

    df = input
    for feature, action in config.items():
        df[feature] = FEATURE_MAP[action](df=df)

    return df
