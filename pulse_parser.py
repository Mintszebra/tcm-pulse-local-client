import re

def parse_pulse_report(text: str):
    """
    Parses a Chinese medicine pulse report text into a structured dictionary.
    """
    # Split the text into blocks based on pulse positions (寸, 關, 尺).
    blocks = re.split(r"---\s*(寸部|關部|尺部).*?---", text)
    result = []

    # Iterate through the blocks to parse each pulse position.
    for i in range(1, len(blocks), 2):
        position = blocks[i].strip()
        content = blocks[i+1]

        data = {
            "position": position,
            "pulse_name": "",
            "heart_rate_bpm": 0,
            "avg_amplitude_pa": 0,
            "avg_pressure_pa": 0,
            "general_meaning": "",
            "specific_symptom": "",
            "recommended_herbs": [],
            "pulse_judgment": "",
            "diagnosis": "",
            "decoction": "",
            "caution": ""
        }

        # --- Quantitative Features ---
        hr = re.search(r"心率\s*\(bpm\)[:：]\s*([\d.]+)", content)
        if hr: data["heart_rate_bpm"] = float(hr.group(1))

        amp = re.search(r"振幅\s*\(Pa\)[:：]\s*([\d.]+)", content)
        if amp: data["avg_amplitude_pa"] = float(amp.group(1))

        press = re.search(r"平均壓力\s*\(Pa\)[:：]\s*([\d.]+)", content)
        if press: data["avg_pressure_pa"] = float(press.group(1))

        # --- Traditional Pulse Name ---
        pulse = re.search(r"初步比對[:：]\s*(\S+)", content)
        if pulse: data["pulse_name"] = pulse.group(1)

        # --- Pulse Judgment ---
        pj = re.search(r"脈象判斷\s*(.*?)(?:證候診斷|個人化用藥|$)", content, re.S)
        if pj: data["pulse_judgment"] = pj.group(1).strip()

        # --- Diagnosis ---
        diag = re.search(r"證候診斷.*?\n(.*?)(?:個人化用藥|$)", content, re.S)
        if diag: data["diagnosis"] = diag.group(1).strip()

        # --- Recommended Herbs (Corrected Logic) ---
        herbs_block = re.search(r"個人化用藥建議 \(含劑量\)(.*?)(?:煎服方法|用藥禁忌|$)", content, re.S)
        if herbs_block:
            # Filter out empty lines, headings, and dividers, including special hyphen characters
            herbs_lines = [line.strip() for line in herbs_block.group(1).split("\n") if line.strip() and not re.match(r'^-+$|^(藥材|劑量|作用)', line.strip()) and '‑' not in line]
            herbs_data = []
            
            # Iterate through lines to find multi-line herb data
            i = 0
            while i < len(herbs_lines):
                line = herbs_lines[i]
                # A line starting with a Chinese character is likely an herb name.
                if re.match(r"^[\u4e00-\u9fa5]+", line):
                    herb_name = line
                    dosage = ""
                    action = ""

                    # The next two lines are expected to be dosage and action.
                    if i + 1 < len(herbs_lines):
                        dosage = herbs_lines[i+1]
                    if i + 2 < len(herbs_lines):
                        action = herbs_lines[i+2]
                    
                    herbs_data.append({
                        "藥材": herb_name,
                        "劑量": dosage,
                        "作用": action
                    })
                    # Skip the next two lines as they've been processed.
                    i += 3
                else:
                    # Skip lines that are not part of the herb data (e.g., headings).
                    i += 1
            data["recommended_herbs"] = herbs_data

        # --- Decoction Method ---
        decoction = re.search(r"煎服方法[:：]\s*(.*?)(?:用藥禁忌|$)", content, re.S)
        if decoction: data["decoction"] = decoction.group(1).strip()

        # --- Caution and Notes ---
        caution = re.search(r"用藥禁忌與注意事項\s*(.*)", content, re.S)
        if caution: data["caution"] = caution.group(1).strip()

        result.append(data)

    return result
