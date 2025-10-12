from analysis_sender import push_analysis_from_text

if __name__ == "__main__":
    with open("test.txt", "r", encoding="utf-8") as f:
        text = f.read()
    push_analysis_from_text(text)
