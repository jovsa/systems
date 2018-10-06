import math

from systems.errors import IllegalSourceStock, InvalidFormula
import systems.lexer

DEFAULT_MAXIMUM = float("+inf")


class Formula:
    """
    Formulas are the core unit of computation in models,
    and are also serve as the interface between lexed formula
    definitions and the underlying models.
    """

    def __init__(self, definition, default=0):
        if type(definition) is str:
            definition = systems.lexer.lex_formula(definition)
            
        self.lexed = definition
        self.default = default
        self.validate()

    def validate(self):
        "Ensure formula is mathematically coherent."
        if type(self.lexed) in (list, tuple):
            tokens = self.lexed[1]
            if len(tokens) == 0:
                raise InvalidFormula(self, "formula is empty. must specify a number or a reference")

            prev_kind = None
            for kind, val in tokens:
                if kind == systems.lexer.TOKEN_OP:
                    if prev_kind == None:
                        raise InvalidFormula(self, "can't start with an operation")
                    elif prev_kind ==  systems.lexer.TOKEN_OP:
                        raise InvalidFormula(self, "operation can't be preceeded by an operation")
                elif prev_kind not in [None, systems.lexer.TOKEN_OP]:
                    raise InvalidFormula(self, "must have an operation between values or references")
                    
                prev_kind = kind
            if prev_kind == systems.lexer.TOKEN_OP:
                raise InvalidFormula(self, "formula cannot end with an operation")

    def references(self):
        "Return list of all references in formula."
        refs = []
        if type(self.lexed) in (list, tuple):
            for kind, val in self.lexed[1]:
                if kind == systems.lexer.TOKEN_REFERENCE:
                    refs.append(val)
        return refs

    def compute(self, state=None):
        if state is None:
            state = {}
            
        # HACK: remove this later and fix things up
        if type(self.lexed) in (int, float):
            return self.lexed

        acc = None
        op = None        
        _, tokens = self.lexed

        # validate() has already ensured that this is a legal formula        
        for token in tokens:
            kind, val_str = token
            if kind == systems.lexer.TOKEN_OP:
                op = val_str
                continue
            if kind == systems.lexer.TOKEN_WHOLE:
                val = int(val_str)
            elif kind  == systems.lexer.TOKEN_DECIMAL:
                val = float(val_str)
            elif kind == systems.lexer.TOKEN_REFERENCE:
                val = state[val_str]
            else:
                Exception("This should be unreachable")
            
            if acc is None:
                acc = val
            elif op == '/':
                acc = acc / val
            elif op == '*':
                acc = acc * val
            elif op == '+':
                acc = acc + val
            elif op == '-':
                acc = acc - val

        return acc if acc else self.default

    def __str__(self):
        "Human readable representation of a Formula."
        if type(self.lexed) in (float, int):
            return "F(%s)" % str(self.lexed)
        else:
            return "F(%s)" % systems.lexer.readable(self.lexed)

    
class Stock(object):
    def __init__(self, name, initial=None, maximum=None, show=True):
        self.name = name
        self.initial = initial if initial else Formula(0)
        self.maximum = maximum if maximum else Formula(float("+inf"))
        self.show = show

    def __repr__(self):
        return "%s(%s)" % (self.__class__.__name__, self.name)


class Flow(object):
    def __init__(self, source, destination, rate):
        self.source = source
        self.destination = destination
        self.rate = rate
        self.rate.validate_source(self.source)

    def change(self, state, source_state, dest_state):
        capacity = self.destination.maximum.compute(state) - dest_state
        return self.rate.calculate(state, source_state, dest_state, capacity)

    def __repr__(self):
        return "%s(%s to %s at %s)" % (self.__class__.__name__,
                                       self.source, self.destination, self.rate)


class Rate(object):
    def __init__(self, formula):
        self.formula = Formula(formula)

    def calculate(self, state, src, dest, capacity):
        evaluated = self.formula.compute(state)
        if src - evaluated >= 0:
            change = evaluated if src - evaluated > 0 else src
            change = min(capacity, change)
            return change, change
        return 0, 0

    def validate_source(self, source_stock):
        "Raise exception is source is not legal."
        return

    def __repr__(self):
        return "%s(%s)" % (self.__class__.__name__, self.formula)


class Conversion(Rate):
    "Converts a stock into another at a discount rate."

    def calculate(self, state, src, dest, capacity):
        evaluated = self.formula.compute(state)
        if dest == float("+inf") or capacity == float("+inf"):
            max_src_change = src
        else:
            max_src_change = max(0, math.floor((capacity - dest) / evaluated))

        change = math.floor(max_src_change * evaluated)
        if change == 0:
            return 0, 0
        return max_src_change, change

    def validate_source(self, source_stock):
        if source_stock.initial.compute() == float("+inf"):
            raise IllegalSourceStock(self, source_stock)


class Leak(Conversion):
    "A stock leaks a percentage of its value into another."

    def calculate(self, state, src, dest, capacity):
        evaluated = self.formula.compute(state)
        change = math.floor(src * evaluated)
        if not math.isnan(capacity):
            change = min(capacity, change)
        return change, change


class State(object):
    def __init__(self, model):
        self.model = model
        self.state = {}
        for stock in self.model.stocks:
            refs = stock.initial.references()
            # TODO: add support for references in initial formula,
            # but for now let's hard reject to avoid it faily in
            # unexpected ways
            if len(refs) > 0:
                raise ReferencesInInitialFormula(stock.initial)
            self.state[stock.name] = stock.initial.compute()

    def advance(self):
        deferred = []

        # HACK: in general better to defer starting at end of list
        # then moving forward to support better pipelining,
        # but it's only by convention that earlier things would
        # be declaed earlier, so this is a weak heuristic at best.
        # would be better to anaylze the graph
        for flow in reversed(self.model.flows):
            source_state = self.state[flow.source.name]
            destination_state = self.state[flow.destination.name]
            rem_change, add_change = flow.change(self.state, source_state, destination_state)
            self.state[flow.source.name] -= rem_change
            deferred.append((flow.destination.name, add_change))

        for dest, change in deferred:
            self.state[dest] += change

    def snapshot(self):
        return self.state.copy()


class Model(object):
    "Models contain and runs stocks and flows."

    def __init__(self, name):
        self.name = name
        self.stocks = []
        self.flows = []

    def get_stock(self, name):
        for stock in self.stocks:
            if stock.name == name:
                return stock

    def infinite_stock(self, name):
        s = Stock(name, Formula(float("+inf")), show=False)
        self.stocks.append(s)
        return s

    def stock(self, *args, **kwargs):
        s = Stock(*args, **kwargs)
        self.stocks.append(s)
        return s

    def flow(self, *args, **kwargs):
        f = Flow(*args, **kwargs)
        self.flows.append(f)
        return f

    def validate(self):
        for stock in self.stocks:
            self.validate_formula(stock.initial)
            self.validate_formula(stock.maximum)

        for flow in self.flows:
            self.validate_formula(flow.rate.formula)

    def validate_formula(self, formula):
        refs = formula.references()
        stocks = [s.name for s in self.stocks]
        for ref in refs:
            if ref not in stocks:
                raise InvalidFormula(formula, "reference to non-existant stock '%s'" % ref)

    def run(self, rounds=10):
        self.validate()

        s = State(self)
        snapshots = [s.snapshot()]
        for i in range(rounds):
            s.advance()
            snapshots.append(s.snapshot())
        return snapshots

    def render_html(self, results):
        rows = ["<table>", "<theader>", "<tr>"]
        col_stocks = [s for s in self.stocks if s.show]
        rows += ["<td><strong>Round</strong></td>"]
        rows += ["<td><strong>%s</strong></td>" % s.name for s in col_stocks]
        rows += ["</tr>", "</theader>", "<tbody>"]

        for i, snapshot in enumerate(results):
            row = "<tr><td>%s</td>" % i
            for j, col in enumerate(col_stocks):
                num = str(snapshot[col.name])
                row += "<td>%s</td>" % num
            row += "</tr>"
            rows.append(row)
        rows += ["</tbody>", "</table>"]
        return "\n".join(rows)

    def render(self, results, sep='\t', pad=True):
        "Render results to string from Model run."
        lines = []

        col_stocks = [s for s in self.stocks if s.show]
        header = sep[:]
        header += sep.join([s.name for s in col_stocks])
        col_size = [len(s.name) for s in col_stocks]
        lines.append(header)

        for i, snapshot in enumerate(results):
            row = "%s" % i
            for j, col in enumerate(col_stocks):
                num = str(snapshot[col.name])
                if pad:
                    num = num.ljust(col_size[j])

                row += sep[:] + num
            lines.append(row)
        return "\n".join(lines)


def main():
    m = Model("Hiring funnel")
    candidates = m.infinite_stock("Candidates")
    screens = m.stock("Phone Screen")
    onsites = m.stock("Onsites")
    offers = m.stock("Offers")
    hires = m.stock("Hires")

    r = Rate(1)
    m.flow(candidates, screens, Rate(2))
    m.flow(screens, onsites, Conversion(0.5))
    m.flow(onsites, offers, Conversion(0.5))
    m.flow(offers, hires, Conversion(0.7))

    rows = m.run()
    print(m.render(rows))


if __name__ == "__main__":
    main()