#!/usr/bin/env python3

FIELDS = ["tool_id", "tool_name", "tool_description", "tool_git_link"]

def collect_tools():
    tools = []
    n = int(input("How many tools do you want to enter? "))

    for i in range(n):
        print(f"\n=== Tool #{i + 1} ===")
        tool = {}

        # tool_id as int
        while True:
            try:
                tool_id_str = input("tool_id (int): ").strip()
                tool["tool_id"] = int(tool_id_str)
                break
            except ValueError:
                print("Please enter a valid integer for tool_id.")

        # other fields as strings
        tool["tool_name"] = input("tool_name: ").strip()
        tool["tool_description"] = input("tool_description: ").strip()
        tool["tool_git_link"] = input("tool_git_link: ").strip()

        tools.append(tool)

    return tools

def tools_to_toon(tools, table_name="tools"):
    n = len(tools)
    header = f"{table_name}[{n}]{{{','.join(FIELDS)}}}:"

    lines = [header]
    for t in tools:
        values = []
        for f in FIELDS:
            v = t[f]
            if isinstance(v, str):
                v = v.replace("\n", "\\n").replace(",", "\\,")
            values.append(str(v))
        lines.append("  " + ",".join(values))

    return "\n".join(lines)

def main():
    tools = collect_tools()
    toon_str = tools_to_toon(tools)

    print("\n=== TOON OUTPUT ===")
    print(toon_str)

    # Save to file
    filename = "tools.toon"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(toon_str)

    print(f"\nSaved TOON data to {filename}")

if __name__ == "__main__":
    main()
