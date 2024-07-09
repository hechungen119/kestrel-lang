import os
import pytest
from uuid import uuid4
from pandas import DataFrame, read_csv

from kestrel.cache import SqlCache
from kestrel.cache.sql import SqlCacheVirtual
from kestrel.ir.graph import IRGraph, IRGraphEvaluable
from kestrel.frontend.parser import parse_kestrel_and_update_irgraph
from kestrel.ir.instructions import Construct
from kestrel.config import load_kestrel_config


@pytest.fixture
def process_creation_events():
    # return a two-node graph:
    #   - Construct: table from logs_ocsf_process_creation.csv
    #   - Variable es: events pointing to the Construct
    graph = IRGraph()
    parse_kestrel_and_update_irgraph("es = NEW event [ {'id': 1} ]", graph, {})
    data_node = graph.get_nodes_by_type(Construct)[0]
    test_dir = os.path.dirname(os.path.abspath(__file__))
    data_node.data = read_csv(os.path.join(test_dir, "logs_kestrelcache_process_creation.csv"))
    return graph


@pytest.fixture
def kestrel_config():
    return load_kestrel_config()


def test_sql_cache_set_get_del():
    c = SqlCache()
    idx = uuid4()
    df = DataFrame({'foo': [1, 2, 3]})
    c[idx] = df
    assert df.equals(c[idx])
    del c[idx]
    assert idx not in c


def test_sql_cache_constructor():
    ids = [uuid4() for i in range(5)]
    df = DataFrame({'foo': [1, 2, 3]})
    c = SqlCache({x:df for x in ids})
    for u in ids:
        assert df.equals(c[u])
    for u in ids:
        del c[u]
        assert u not in c


def test_eval_new_disp():
    stmt = """
proclist = NEW process [ {"name": "cmd.exe", "pid": 123}
                       , {"name": "explorer.exe", "pid": 99}
                       , {"name": "firefox.exe", "pid": 201}
                       , {"name": "chrome.exe", "pid": 205}
                       ]
DISP proclist ATTR name
"""
    graph = IRGraph()
    rets = parse_kestrel_and_update_irgraph(stmt, graph, {})
    graph = IRGraphEvaluable(graph)
    c = SqlCache()
    mapping = c.evaluate_graph(graph, c)

    # check the return is correct
    assert len(rets) == 1
    df = mapping[rets[0].id]
    assert df.to_dict("records") == [ {"name": "cmd.exe"}
                                    , {"name": "explorer.exe"}
                                    , {"name": "firefox.exe"}
                                    , {"name": "chrome.exe"}
                                    ]


def test_eval_new_filter_disp():
    stmt = """
proclist = NEW process [ {"name": "cmd.exe", "pid": 123}
                       , {"name": "explorer.exe", "pid": 99}
                       , {"name": "firefox.exe", "pid": 201}
                       , {"name": "chrome.exe", "pid": 205}
                       ]
browsers = proclist WHERE name = 'firefox.exe' OR name = 'chrome.exe'
DISP browsers ATTR name, pid
"""
    graph = IRGraph()
    rets = parse_kestrel_and_update_irgraph(stmt, graph, {})
    graph = IRGraphEvaluable(graph)
    c = SqlCache()
    mapping = c.evaluate_graph(graph, c)

    # check the return is correct
    assert len(rets) == 1
    df = mapping[rets[0].id]
    assert df.to_dict("records") == [ {"name": "firefox.exe", "pid": 201}
                                    , {"name": "chrome.exe", "pid": 205}
                                    ]

    
def test_eval_two_returns():
    stmt = """
proclist = NEW process [ {"name": "cmd.exe", "pid": 123}
                       , {"name": "explorer.exe", "pid": 99}
                       , {"name": "firefox.exe", "pid": 201}
                       , {"name": "chrome.exe", "pid": 205}
                       ]
browsers = proclist WHERE name != "cmd.exe"
DISP browsers
DISP browsers ATTR pid
"""
    graph = IRGraph()
    rets = parse_kestrel_and_update_irgraph(stmt, graph, {})
    graph = IRGraphEvaluable(graph)
    c = SqlCache()

    # first DISP
    gs = graph.find_dependent_subgraphs_of_node(rets[0], c)
    assert len(gs) == 1
    mapping = c.evaluate_graph(gs[0], c)
    df1 = DataFrame([ {"name": "explorer.exe", "pid": 99}
                    , {"name": "firefox.exe", "pid": 201}
                    , {"name": "chrome.exe", "pid": 205}
                    ])
    assert len(mapping) == 1
    assert df1.equals(mapping[rets[0].id])

    # second DISP
    gs = graph.find_dependent_subgraphs_of_node(rets[1], c)
    assert len(gs) == 1
    mapping = c.evaluate_graph(gs[0], c)
    df2 = DataFrame([ {"pid": 99}
                    , {"pid": 201}
                    , {"pid": 205}
                    ])
    assert len(mapping) == 1
    assert df2.equals(mapping[rets[1].id])


def test_issue_446():
    """The `WHERE name IN ...` below was raising `sqlalchemy.exc.StatementError: (builtins.KeyError) 'name_1'`
    https://github.com/opencybersecurityalliance/kestrel-lang/issues/446
    """
    stmt = """
proclist = NEW process [ {"name": "cmd.exe", "pid": 123}
                       , {"name": "explorer.exe", "pid": 99}
                       , {"name": "firefox.exe", "pid": 201}
                       , {"name": "chrome.exe", "pid": 205}
                       ]
browsers = proclist WHERE name IN ("explorer.exe", "firefox.exe", "chrome.exe")
"""
    graph = IRGraph()
    rets = parse_kestrel_and_update_irgraph(stmt, graph, {})
    graph = IRGraphEvaluable(graph)
    c = SqlCache()
    _ = c.evaluate_graph(graph, c)


def test_eval_filter_with_ref():
    stmt = """
proclist = NEW process [ {"name": "cmd.exe", "pid": 123}
                       , {"name": "explorer.exe", "pid": 99}
                       , {"name": "firefox.exe", "pid": 201}
                       , {"name": "chrome.exe", "pid": 205}
                       ]
browsers = proclist WHERE name = 'firefox.exe' OR name = 'chrome.exe'
specials = proclist WHERE pid IN [123, 201]
p2 = proclist WHERE pid = browsers.pid and name = specials.name
DISP p2 ATTR name, pid
"""
    graph = IRGraph()
    rets = parse_kestrel_and_update_irgraph(stmt, graph, {})
    graph = IRGraphEvaluable(graph)
    c = SqlCache()
    mapping = c.evaluate_graph(graph, c)

    # check the return is correct
    assert len(rets) == 1
    df = mapping[rets[0].id]
    assert df.to_dict("records") == [ {"name": "firefox.exe", "pid": 201} ]


def test_get_virtual_copy():
    stmt = """
proclist = NEW process [ {"name": "cmd.exe", "pid": 123}
                       , {"name": "explorer.exe", "pid": 99}
                       , {"name": "firefox.exe", "pid": 201}
                       , {"name": "chrome.exe", "pid": 205}
                       ]
browsers = proclist WHERE name = 'firefox.exe' OR name = 'chrome.exe'
"""
    graph = IRGraph()
    rets = parse_kestrel_and_update_irgraph(stmt, graph, {})
    graph = IRGraphEvaluable(graph)
    c = SqlCache()
    mapping = c.evaluate_graph(graph, c)
    v = c.get_virtual_copy()
    new_entry = uuid4()
    v[new_entry] = True

    # v[new_entry] calls the right method
    assert isinstance(v, SqlCacheVirtual)
    assert v[new_entry].endswith("v")

    # the two cache_catalog are different
    assert new_entry not in c
    assert new_entry in v
    del v[new_entry]
    assert new_entry not in v
    for u in c:
        del v[u]
    assert len(v) == 0
    assert len(c) == 1


def test_eval_find_event_to_entity(process_creation_events):
    graph = process_creation_events
    stmt = "procs = FIND process RESPONDED es WHERE device.os = 'Linux' DISP procs"
    rets = parse_kestrel_and_update_irgraph(stmt, graph, {})
    graph = IRGraphEvaluable(graph)
    c = SqlCache()
    mapping = c.evaluate_graph(graph, c)
    assert len(rets) == 1
    df = mapping[rets[0].id]
    assert list(df.columns) == ['cmd_line', 'name', 'pid', 'uid', 'endpoint.uid', 'endpoint.name',
       'endpoint.os', 'file.name', 'file.path', 'user.uid', 'user.name',
       'user.type_id', 'user.endpoint.uid', 'user.endpoint.name',
       'user.endpoint.os', 'file.endpoint.uid', 'file.endpoint.name',
       'file.endpoint.os']
    assert df.shape[0] == 5  # WHERE clause filtered out 4 out of 9, so 5 remains


def test_eval_find_entity_to_event(process_creation_events, kestrel_config):
    graph = process_creation_events
    stmt = """
        procs = FIND process RESPONDED es WHERE device.os = 'Linux'
        eves = FIND event ORIGINATED BY procs
        DISP eves
    """
    rets = parse_kestrel_and_update_irgraph(stmt, graph, kestrel_config["entity_identifier"])
    graph = IRGraphEvaluable(graph)
    c = SqlCache()
    mapping = c.evaluate_graph(graph, c)
    assert len(rets) == 1
    df = mapping[rets[0].id]

    # 1. WHERE clause filtered out 4 out of 9, so 5 remains
    # 2. In the 5, 4 are not parent process of others, only the first is parent process
    # 3. There are 4 lines of logs/events that uses the parent process
    assert df.shape[0] == 4
    assert df.shape[1] == 41  # full event: the number of columns in the csv


def test_eval_find_entity_to_entity(process_creation_events, kestrel_config):
    graph = process_creation_events
    stmt = """
        procs = FIND process RESPONDED es WHERE device.os = 'Linux'
        parents = FIND process CREATED procs
        DISP parents
    """
    rets = parse_kestrel_and_update_irgraph(stmt, graph, kestrel_config["entity_identifier"])
    graph = IRGraphEvaluable(graph)
    c = SqlCache()
    mapping = c.evaluate_graph(graph, c)
    assert len(rets) == 1
    df = mapping[rets[0].id]

    # 1. WHERE clause filtered out 4 out of 9, so 5 remains
    # 2. The last 4 share the same parent
    # 3. So there are 2 processes returned/displayed after dedup
    assert df.shape[0] == 2
    assert list(df.columns) == ['cmd_line', 'name', 'pid', 'uid', 'endpoint.uid', 'endpoint.name', 'endpoint.os']
