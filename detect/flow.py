from collections import Counter


def flow_edges(leavers, anchor_label: str):
    dest = Counter()
    for p in leavers:
        cur = p.current_role
        if cur and (cur.company_name or cur.company_id):
            dest[(cur.company_id, cur.company_name)] += 1
    return [{"source": anchor_label, "target_id": cid, "target": name, "weight": w}
            for (cid, name), w in dest.most_common()]
