import re
import os
import time
import logging
try:
    logger = logging.getLogger("search-report")
except:
    pass
from collections import defaultdict
import base64
try:
    from lxml import etree as ET
except:
    try:
        from xml.etree import cElementTree as ET
    except:
        from xml.etree import ElementTree as ET

import matplotlib
from matplotlib import pyplot as plt

from jinja2 import Environment, PackageLoader, Undefined, FileSystemLoader
from jinja2 import nodes
from jinja2.ext import Extension

from glypy.composition import composition_transform
from glypy.utils import StringIO
from glypy import plot

from glypy.search.spectra import spectrum_model

matplotlib.rcParams['svg.fonttype'] = 'path'


def collect_fragments(record):
    matches = defaultdict(list)
    for match in record.matches:
        matches[match.match_key.split(":")[0]].append(match)
    return matches.keys()


def fetch_all_matches(db, attr='mass', order="ASC", threshold=0.0):
    order_by = " ORDER BY {} {}".format(attr, order)
    return db.from_sql(db.execute("SELECT * FROM {table_name} WHERE \
        (tandem_scan_count + precursor_scan_count) > 0 AND tandem_score >= {threshold} {order_by};".format(
            table_name=db.record_type.table_name, threshold=threshold, order_by=order_by)))


def strip_derivatize_glycoct(record):
    s = record.structure.clone()
    composition_transform.strip_derivatization(s)
    return(str(s))


def cfg_plot(record):
    if "svg_plot" in record.report_data:
        return base64.decodestring(record.report_data["svg_plot"])
    s = record.structure.clone()
    composition_transform.strip_derivatization(s)
    dtree, ax = plot.plot(s, orientation='h', squeeze=1.4, scale=.135)
    fmap = {f.name: f for f in record.fragments}
    for match in record.matches:
        match_key = match.match_key.split(":")[0]
        order = len(match_key.split("-"))
        if order == 1:
            dtree.draw_cleavage(ax=ax, fragment=fmap[match_key], color='red', label=True)
        else:
            for key in match_key.split("-"):
                dtree.draw_cleavage(fragment=fmap[key], ax=ax, color='orange', label=True)

    ax.axis('off')
    fig = ax.get_figure()
    fig.tight_layout(pad=0.2)
    img_buffer = StringIO()
    fig.savefig(img_buffer, format="svg")
    plt.close(fig)

    root, ids = ET.XMLID(img_buffer.getvalue())
    root.set("id", dtree.uuid)
    svg = ET.tostring(root)
    record.report_data["svg_plot"] = base64.encodestring(svg)
    record.update()
    return svg


def simple_plot(record):
    dtree, ax = plot.plot(record, orientation='h', squeeze=1.4, scale=.135)
    ax.axis('off')
    fig = ax.get_figure()
    fig.tight_layout(pad=0.2)
    img_buffer = StringIO()
    fig.savefig(img_buffer, format="svg")
    plt.close(fig)
    root, ids = ET.XMLID(img_buffer.getvalue())
    root.set("id", dtree.uuid)
    svg = ET.tostring(root)
    return svg


greek_symbol_map = {
    "a": "&alpha;",
    "b": "&beta;",
    "c": "&gamma;",
    "d": "&delta;",
    "e": "&epsilon;",
    "f": "&zeta;",
    "g": "&eta;",
    "h": "&iota;",
    "i": "&kappa;",
    "j": "&lambda;",
    "k": "&mu;",
    "l": "&nu;",
    "m": "&xi;",
    "n": "&varsigma;",
    "o": "&pi;",
    "p": "&rho;",
    "q": "&sigma;",
    "r": "&tau;",
    "s": "&upsilon;",
    "t": "&phi;",
    "u": "&psi;",
    "v": "&omega;"
}


def greek_fragment_names(fragment_name):
    buff = []
    for c in fragment_name:
        if c in greek_symbol_map:
            buff.append(greek_symbol_map[c])
        else:
            buff.append(c)
    return ''.join(buff)


def spectrum_plot(precursor_spectrum):
    fig = spectrum_model.plot_observed_spectra(precursor_spectrum, annotation_source=None)
    img_buffer = StringIO()
    fig.savefig(img_buffer, format='svg')
    plt.close(fig)
    root, ids = ET.XMLID(img_buffer.getvalue())
    svg = ET.tostring(root)
    return svg


def scientific_notation(num):
    if num is None or isinstance(num, Undefined):
        return "N/A"
    return "%0.3e" % num


def limit_sigfig(num):
    if num is None or isinstance(num, Undefined):
        return "N/A"
    return "%0.4f" % num


def unique(iterable):
    return set(iterable)


def css_escape(css_string):
    return re.sub(r"[\+\:,\s]", r'-', css_string)


def prepare_environment(env=None):
    try:
        loader = PackageLoader("glypy", "search/results_template")
        loader.list_templates()
    except:
        loader = FileSystemLoader(os.path.join(os.path.dirname(__file__), 'results_template'))
    if env is None:
        env = Environment(loader=loader, extensions=[FragmentCacheExtension])
    else:
        env.loader = loader
        env.add_extension(FragmentCacheExtension)
    env.filters["collect_fragments"] = collect_fragments
    env.filters["all_matches"] = fetch_all_matches
    env.filters["strip_derivatize"] = strip_derivatize_glycoct
    env.filters["scientific_notation"] = scientific_notation
    env.filters["cfg_plot"] = cfg_plot
    env.filters["simple_cfg_plot"] = simple_plot
    env.filters["min"] = min
    env.filters["max"] = max
    env.filters["unique"] = unique
    env.filters["limit_sigfig"] = limit_sigfig
    env.filters['css_escape'] = css_escape
    env.filters['greek_fragment_name'] = greek_fragment_names
    env.fragment_cache = dict()
    return env


def prepare_template():
    env = prepare_environment()

    template = env.get_template("results.templ")
    return template


def render(database, experimental_statistics=None, settings=None, live=False, **kwargs):
    template = prepare_template()
    return template.render(
        database=database,
        experimental_statistics=experimental_statistics,
        settings=settings, live=live, **kwargs)


class FragmentCacheExtension(Extension):
    # a set of names that trigger the extension.
    tags = set(['cache'])

    def __init__(self, environment):
        super(FragmentCacheExtension, self).__init__(environment)

        # add the defaults to the environment
        environment.extend(
            fragment_cache_prefix='',
            fragment_cache=None
        )

    def parse(self, parser):
        # the first token is the token that started the tag.  In our case
        # we only listen to ``'cache'`` so this will be a name token with
        # `cache` as value.  We get the line number so that we can give
        # that line number to the nodes we create by hand.
        lineno = parser.stream.next().lineno

        # now we parse a single expression that is used as cache key.
        args = [parser.parse_expression()]

        # if there is a comma, the user provided a timeout.  If not use
        # None as second parameter.
        if parser.stream.skip_if('comma'):
            args.append(parser.parse_expression())
        else:
            args.append(nodes.Const(None))

        # now we parse the body of the cache block up to `endcache` and
        # drop the needle (which would always be `endcache` in that case)
        body = parser.parse_statements(['name:endcache'], drop_needle=True)

        # now return a `CallBlock` node that calls our _cache_support
        # helper method on this extension.
        return nodes.CallBlock(self.call_method('_cache_support', args),
                               [], [], body).set_lineno(lineno)

    def _cache_support(self, name, timeout, caller):
        """Helper callback."""
        key = self.environment.fragment_cache_prefix + name
        print("{} - In cache for {}".format(time.time(), name))
        print(self.environment.fragment_cache.keys())
        # try to load the block from the cache
        # if there is no fragment in the cache, render it and store
        # it in the cache.
        rv = self.environment.fragment_cache.get(key)
        if rv is not None:
            print "Cache Hit"
            return rv
        print "Cache Miss"
        rv = caller()
        self.environment.fragment_cache[key] = rv
        return rv
