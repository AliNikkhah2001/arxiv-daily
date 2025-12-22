import os
import re
import json
import arxiv
import yaml
import logging
import argparse
import datetime
import requests

logging.basicConfig(
    format='[%(asctime)s %(levelname)s] %(message)s',
    datefmt='%m/%d/%Y %H:%M:%S',
    level=logging.INFO
)

github_url = "https://api.github.com/search/repositories"
arxiv_url = "http://arxiv.org/"

# =========================================================
# Safe JSON utilities (CI-proof)
# =========================================================

def safe_load_json(filename: str) -> dict:
    if not os.path.exists(filename):
        logging.warning(f"{filename} not found. Initializing new file.")
        return {}

    try:
        with open(filename, "r") as f:
            content = f.read().strip()
            if not content:
                return {}
            return json.loads(content)
    except json.JSONDecodeError:
        logging.warning(f"{filename} contains invalid JSON. Resetting.")
        return {}
    except Exception as e:
        logging.warning(f"Failed reading {filename}: {e}. Resetting.")
        return {}


def safe_write_json(filename: str, data: dict):
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    tmp_file = filename + ".tmp"
    with open(tmp_file, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_file, filename)

# =========================================================
# Config loading
# =========================================================

def load_config(config_file: str) -> dict:
    def pretty_filters(**config) -> dict:
        keywords = {}
        OR = " OR "

        def parse_filters(filters):
            parts = []
            for f in filters:
                if " " in f:
                    parts.append(f"\"{f}\"")
                else:
                    parts.append(f)
            return OR.join(parts)

        for k, v in config["keywords"].items():
            keywords[k] = parse_filters(v["filters"])
        return keywords

    with open(config_file, "r") as f:
        config = yaml.load(f, Loader=yaml.FullLoader)
        config["kv"] = pretty_filters(**config)
        logging.info(f"config = {config}")
    return config

# =========================================================
# Helpers
# =========================================================

def get_authors(authors, first_author=False):
    return authors[0] if first_author else ", ".join(str(a) for a in authors)


def sort_papers(papers):
    return dict(sorted(papers.items(), reverse=True))


def get_code_link(qword: str) -> str | None:
    params = {"q": qword, "sort": "stars", "order": "desc"}
    r = requests.get(github_url, params=params, timeout=10)
    results = r.json()
    if results.get("total_count", 0) > 0:
        return results["items"][0]["html_url"]
    return None

# =========================================================
# arXiv fetch (deprecation-safe)
# =========================================================

def get_daily_papers(topic, query, max_results):
    content = {}
    content_web = {}

    search = arxiv.Search(
        query=query,
        max_results=max_results,
        sort_by=arxiv.SortCriterion.SubmittedDate
    )

    client = arxiv.Client()

    for result in client.results(search):
        paper_id = result.get_short_id().split("v")[0]
        title = result.title
        first_author = get_authors(result.authors, True)
        update_time = result.updated.date()
        paper_url = f"{arxiv_url}abs/{paper_id}"

        logging.info(f"Time = {update_time} title = {title} author = {first_author}")

        content[paper_id] = (
            f"|**{update_time}**|**{title}**|{first_author} et.al.|"
            f"[{paper_id}]({paper_url})|null|\n"
        )

        content_web[paper_id] = (
            f"- {update_time}, **{title}**, {first_author} et.al., "
            f"Paper: [{paper_url}]({paper_url})\n"
        )

    return {topic: content}, {topic: content_web}

# =========================================================
# JSON update logic
# =========================================================

def update_json_file(filename, data_dict):
    json_data = safe_load_json(filename)

    for block in data_dict:
        for keyword, papers in block.items():
            json_data.setdefault(keyword, {}).update(papers)

    safe_write_json(filename, json_data)


def update_paper_links(filename):
    json_data = safe_load_json(filename)

    def parse_row(s):
        parts = s.split("|")
        return parts[1:6]

    for keyword, papers in json_data.items():
        for pid, row in papers.items():
            date, title, author, url, code = parse_row(row)
            papers[pid] = f"|{date}|{title}|{author}|{url}|{code}|\n"

    safe_write_json(filename, json_data)

# =========================================================
# Markdown generation
# =========================================================

def json_to_md(filename, md_filename, task="", to_web=False,
               use_title=True, use_tc=True, show_badge=True, use_b2t=True):

    data = safe_load_json(filename)

    DateNow = datetime.date.today().strftime("%Y.%m.%d")

    with open(md_filename, "w", encoding="utf-8") as f:
        if use_title:
            f.write(f"## Updated on {DateNow}\n\n")

        if use_tc:
            f.write("<details><summary>Table of Contents</summary><ol>\n")
            for k in data:
                f.write(f"<li><a href=#{k.lower().replace(' ','-')}>{k}</a></li>\n")
            f.write("</ol></details>\n\n")

        for keyword, papers in data.items():
            if not papers:
                continue

            f.write(f"## {keyword}\n\n")
            f.write("|Publish Date|Title|Authors|PDF|Code|\n")
            f.write("|---|---|---|---|---|\n")

            for _, row in sort_papers(papers).items():
                f.write(row)

            if use_b2t:
                f.write("\n<p align=right>(<a href=#updated-on>back to top</a>)</p>\n\n")

    logging.info(f"{task} finished")

# =========================================================
# Main pipeline
# =========================================================

def demo(**config):
    data_collector = []
    data_collector_web = []

    if not config["update_paper_links"]:
        for topic, keyword in config["kv"].items():
            data, data_web = get_daily_papers(
                topic, keyword, config["max_results"]
            )
            data_collector.append(data)
            data_collector_web.append(data_web)

    if config["publish_readme"]:
        jf, mf = config["json_readme_path"], config["md_readme_path"]
        update_json_file(jf, data_collector)
        json_to_md(jf, mf, task="Update Readme")

    if config["publish_gitpage"]:
        jf, mf = config["json_gitpage_path"], config["md_gitpage_path"]
        update_json_file(jf, data_collector)
        json_to_md(jf, mf, task="Update GitPage", to_web=True,
                   use_tc=False, use_b2t=False)

    if config["publish_wechat"]:
        jf, mf = config["json_wechat_path"], config["md_wechat_path"]
        update_json_file(jf, data_collector_web)
        json_to_md(jf, mf, task="Update Wechat",
                   use_title=False)

# =========================================================
# Entry point
# =========================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config_path", default="config.yaml")
    parser.add_argument("--update_paper_links", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config_path)
    config["update_paper_links"] = args.update_paper_links

    demo(**config)
