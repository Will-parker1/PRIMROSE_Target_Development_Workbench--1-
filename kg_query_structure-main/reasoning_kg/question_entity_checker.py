from rapidfuzz import process, fuzz

def entity_checking(question, nlp_model, nodes):

    doc = nlp_model(question)
    adapted_sentence = question
    successful_matches = []

    # Extract named entities
    test_phrases = [ent.text for ent in doc.ents]

    # Check anything with special characters.
    test_phrases.extend([w for w in question[-1].split() if not w.isalnum() and w not in test_phrases])

    # 
    for chunk in doc.noun_chunks:
        if chunk.text not in test_phrases:
            test_phrases.append(chunk.text)
    if not test_phrases:
        test_phrases = [question[-1]]

    print(test_phrases)
    
    
    for ent in test_phrases:
        match = process.extractOne(
            ent, 
            nodes, 
            scorer=fuzz.token_sort_ratio, 
            score_cutoff= 70
        )
        
        if match:
            best_match_str, score, _ = match
            adapted_sentence = adapted_sentence.replace(ent, best_match_str)
            successful_matches.append(best_match_str)

    return adapted_sentence, successful_matches