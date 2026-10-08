#!/usr/bin/env python3
"""
Explore the boundaries of the environment in .env - to pick HCM_HIERARCHY_TYPE,
HCM_BOUNDARY and HCM_USER_LEVEL for a new environment without the UI.

    ./list_boundaries.py                              # hierarchies, levels, tree, what .env selects
    ./list_boundaries.py --depth 5                    # deeper tree
    ./list_boundaries.py --under NIGERIA_NI_02_KOGI   # the subtree of one boundary
    ./list_boundaries.py --type DISTRICT              # every boundary of one level, with its path
    ./list_boundaries.py --hierarchy MICROPLAN        # another hierarchy than HCM_HIERARCHY_TYPE

Read-only. Codes are what HCM_BOUNDARY takes; level names are what
HCM_USER_LEVEL and HCM_REGISTER_LEVEL take.
"""
import argparse

import utils.campaign as C
import utils.hcm as H


def print_tree(node, depth, max_children, indent=0):
    children = node.get("children") or []
    count = f"  ({len(children)} below)" if children and depth == 0 else ""
    print(f"           {'  ' * indent}{node.get('code')}  [{node.get('boundaryType')}]{count}")
    if depth == 0:
        return
    for child in children[:max_children]:
        print_tree(child, depth - 1, max_children, indent + 1)
    if len(children) > max_children:
        print(f"           {'  ' * (indent + 1)}… {len(children) - max_children} more"
              f" (--max-children, or --under {node.get('code')})")


def of_type(node, level, path=()):
    """(code, path) of every node of boundary type ``level`` under node."""
    path = path + (node.get("code"),)
    if (node.get("boundaryType") or "").upper() == level:
        yield node.get("code"), path
    for child in node.get("children") or []:
        yield from of_type(child, level, path)


def show_selection(hcm, hierarchy):
    """What campaign creation would select with the current .env - or why it
    would fail - so a wrong code or level shows up here, not mid-run."""
    print("\n== what .env selects")
    source = (f"HCM_BOUNDARIES_FROM={H.BOUNDARIES_FROM}" if H.BOUNDARIES_FROM else
              f"HCM_BOUNDARY={H.BOUNDARY}" if H.BOUNDARY else
              "neither set - the first root-to-leaf path")
    print(f"           source     {source}")
    try:
        boundaries, valid, target = C.resolve_selection(hcm, hierarchy, H.BOUNDARY,
                                                        H.BOUNDARIES_FROM)
        print(f"           boundaries {' > '.join(b['code'] for b in boundaries)}"
              f"  (+ children: {len(valid) - len(boundaries)})")
        print(f"           users      {C.user_level_boundary(boundaries, H.USER_LEVEL)}"
              f"  (HCM_USER_LEVEL={H.USER_LEVEL})")
        print(f"           facilities re-pointed to {target} when outside the campaign")
    except SystemExit as e:
        print(f"  PROBLEM  {str(e).strip().removeprefix('ERROR').strip()}")


def main():
    ap = argparse.ArgumentParser(description="List hierarchies, levels and boundary codes.")
    ap.add_argument("--hierarchy", default=H.HIERARCHY_TYPE,
                    help="hierarchy type (default HCM_HIERARCHY_TYPE)")
    ap.add_argument("--under", metavar="CODE", help="show the subtree of this boundary")
    ap.add_argument("--type", metavar="LEVEL", help="list every boundary of this level")
    ap.add_argument("--depth", type=int, default=3, help="tree levels to show (default 3)")
    ap.add_argument("--max-children", type=int, default=10,
                    help="children shown per boundary (default 10)")
    args = ap.parse_args()

    hcm, _ = H.connect()
    hierarchies = C.list_hierarchies(hcm)
    print(f"\n== hierarchies in tenant {H.TENANT}\n           {', '.join(hierarchies) or 'none'}")
    hierarchy = args.hierarchy or (H.BOUNDARIES_FROM and C.resolve_hierarchy(
        hcm, None, H.BOUNDARIES_FROM))
    if not hierarchy:
        H.die("set HCM_HIERARCHY_TYPE in .env (one of the above) or pass --hierarchy")
    if hierarchy not in hierarchies:
        H.die(f"hierarchy {hierarchy} is not in tenant {H.TENANT}: {hierarchies}")

    levels = hcm.hierarchy_levels(hierarchy)
    print(f"\n== {hierarchy} levels (for HCM_USER_LEVEL / HCM_REGISTER_LEVEL)")
    print("           " + " > ".join(f"{i}={t}" for i, t in enumerate(levels, 1)))

    roots = C.boundary_tree(hcm, hierarchy, args.under)
    if not roots:
        H.die(f"no boundary {args.under or ''} in hierarchy {hierarchy}".replace("  ", " "))
    if args.type:
        level = args.type.upper()
        if level not in levels:
            H.die(f"{level} is not a level of {hierarchy}: {levels}")
        found = [hit for root in roots for hit in of_type(root, level)]
        print(f"\n== {len(found)} {level} boundaries"
              + (f" under {args.under}" if args.under else ""))
        for code, path in found:
            print(f"           {code}    ({' > '.join(path[:-1])})")
    else:
        print(f"\n== boundaries{' under ' + args.under if args.under else ''}"
              f" (depth {args.depth}; codes for HCM_BOUNDARY)")
        for root in roots:
            print_tree(root, args.depth, args.max_children)

    if hierarchy == (H.HIERARCHY_TYPE or hierarchy):
        show_selection(hcm, hierarchy)
    print()


if __name__ == "__main__":
    try:
        main()
    except H.ApiError as e:
        H.die(str(e))
    except KeyboardInterrupt:
        raise SystemExit(130)
