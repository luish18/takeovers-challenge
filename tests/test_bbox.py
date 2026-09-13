from takeovers_challenge import bbox, corpus


def test_polygon_to_norm_matches_bbox_viewer_grep():
    # Expected values printed by `tools/bbox_viewer.py --grep "capital social"` on 2018-05-30, page 3.
    expected = {
        "p3.5": [0.1222, 0.2212, 0.8914, 0.2394],
        "p3.9": [0.1230, 0.3363, 0.4143, 0.3537],
        "p3.10": [0.1234, 0.3677, 0.5788, 0.3854],
    }
    doc = corpus.load_document(
        next((corpus.config.DATA_DIR / "480489707/actes/meta").glob("*63e9593b8be6eb9f9d257ec0.json"))
    )
    for line_id, box in expected.items():
        page, line = doc.lookup(line_id)
        assert bbox.rounded(page.norm_box(line)) == box


def test_union_and_clamp():
    assert bbox.union([[0.1, 0.2, 0.5, 0.3], [0.2, 0.25, 0.9, 0.4]]) == [0.1, 0.2, 0.9, 0.4]
    assert bbox.polygon_to_norm([[-5, -5], [5000, -5], [5000, 9000], [-5, 9000]], 595, 842) == [0, 0, 1, 1]
