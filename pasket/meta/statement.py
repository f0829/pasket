import cStringIO
from functools import partial
import operator as op

import antlr3
from antlr3.tree import CommonTree as AST
import grammar.JavaLexer as Lexer
import grammar.JavaParser as Parser

from lib.typecheck import *
import lib.const as C
import lib.visit as v

from .. import util

from expression import Expression, parse_e, gen_E_id

# s ::= e | assert e | return e? | e := e | if e s s?
#     | while e s | repeat e s # syntactic sugar borrowed from sketch
#     | for e e s | try s (catch e s)* (finally s)?
#     | s; s # represented as a list
C.S = util.enum("EXP", "ASSERT", "RETURN", "ASSIGN", "IF", "WHILE", "REPEAT", "FOR", "TRY")


class Statement(v.BaseNode):

  def __init__(self, k, **kwargs):
    self._kind = k
    for key in kwargs:
      setattr(self, key, kwargs[key])

  @property
  def kind(self):
    return self._kind

  def __str__(self, e_printer=str):
    curried = lambda st: st.__str__(e_printer)
    buf = cStringIO.StringIO()
    if self._kind == C.S.EXP:
      buf.write(e_printer(self.e) + ';')

    elif self._kind == C.S.ASSERT:
      buf.write("assert " + e_printer(self.e) + ';')

    elif self._kind == C.S.RETURN:
      buf.write("return")
      if hasattr(self, "e"): buf.write(' '+ e_printer(self.e))
      buf.write(';')

    elif self._kind == C.S.ASSIGN:
      buf.write(e_printer(self.le) + " = " + e_printer(self.re) + ';')

    elif self._kind == C.S.IF:
      e = e_printer(self.e)
      t = '\n'.join(map(curried, self.t))
      f = '\n'.join(map(curried, self.f))
      buf.write("if (" + e + ") {\n" + t + "\n}")
      if f: buf.write("\nelse {\n" + f + "\n}")

    elif self._kind == C.S.WHILE:
      e = e_printer(self.e)
      b = '\n'.join(map(curried, self.b))
      buf.write("while (" + e + ") {\n" + b + "\n}")

    elif self._kind == C.S.REPEAT:
      e = e_printer(self.e)
      b = '\n'.join(map(curried, self.b))
      buf.write("repeat (" + e + ") {\n" + b + "\n}")

    elif self._kind == C.S.FOR:
      e_def = e_printer(self.i)
      e_iter = e_printer(self.init)
      b = '\n'.join(map(curried, self.b))
      buf.write("for (" + e_def + " : " + e_iter + ") {\n" + b + "\n}")

    elif self._kind == C.S.TRY:
      b = '\n'.join(map(curried, self.b))
      buf.write("try {\n" + b + "\n}")
      for (e, cs) in self.catches:
        e = e_printer(e)
        cs = '\n'.join(map(curried, cs))
        buf.write("catch (" + e + ") {\n" + cs + "\n}")
      fs = '\n'.join(map(curried, self.fs))
      buf.write("finally {\n" + fs + "\n}")

    return buf.getvalue()

  def __repr__(self):
    return self.__str__()

  def accept(self, visitor):
    f = op.methodcaller("accept", visitor)
    if self._kind in [C.S.EXP, C.S.ASSERT, C.S.RETURN]:
      if hasattr(self, "e"):
        v_e = f(self.e)
        if type(v_e) is list: return v_e
        else: self.e = v_e
    elif self._kind == C.S.ASSIGN:
      self.le = f(self.le)
      self.re = f(self.re)
    elif self._kind == C.S.IF:
      self.e = f(self.e)
      self.t = util.flatten(map(f, self.t))
      self.f = util.flatten(map(f, self.f))
    elif self._kind in [C.S.WHILE, C.S.REPEAT]:
      self.e = f(self.e)
      self.b = util.flatten(map(f, self.b))
    elif self._kind == C.S.FOR:
      self.i = f(self.i)
      self.init = f(self.init)
      self.b = util.flatten(map(f, self.b))
    elif self._kind == C.S.TRY:
      self.b = util.flatten(map(f, self.b))
      self.catches = [ ( f(e), util.flatten(map(f, cs)) ) for (e, cs) in self.catches ]
      self.fs = util.flatten(map(f, self.fs))
    return visitor.visit(self)


# e -> S(EXP, e)
@takes(Expression)
@returns(Statement)
def gen_S_e(e):
  return Statement(C.S.EXP, e=e)


# e -> S(ASSERT, e)
@takes(Expression)
@returns(Statement)
def gen_S_assert(e):
  return Statement(C.S.ASSERT, e=e)


# e -> S(RETURN, e)
@takes(optional(Expression))
@returns(Statement)
def gen_S_rtn(e=None):
  if e: return Statement(C.S.RETURN, e=e)
  else: return Statement(C.S.RETURN)


# le, re -> S(ASSIGN, le, re)
@takes(Expression, Expression)
@returns(Statement)
def gen_S_assign(le, re):
  return Statement(C.S.ASSIGN, le=le, re=re)


# e, ts, fs -> S(IF, e, ts, fs)
@takes(Expression, list_of(Statement), list_of(Statement))
@returns(Statement)
def gen_S_if(e, ts, fs):
  return Statement(C.S.IF, e=e, t=ts, f=fs)


# e, ss -> S(WHILE, e, ss)
@takes(Expression, list_of(Statement))
@returns(Statement)
def gen_S_while(e, ss):
  return Statement(C.S.WHILE, e=e, b=ss)


# e, ss -> S(REPEAT, e, ss)
@takes(Expression, list_of(Statement))
@returns(Statement)
def gen_S_repeat(e, ss):
  return Statement(C.S.REPEAT, e=e, b=ss)


# e_def, e_init, ss -> S(FOR, e_def, e_init, ss)
@takes(Expression, Expression, list_of(Statement))
@returns(Statement)
def gen_S_for(e_def, e_init, ss):
  return Statement(C.S.FOR, i=e_def, init=e_init, b=ss)


# ss, catches, fs -> S(TRY, ss, catches, fs)
@takes(list_of(Statement), list_of(anything), list_of(Statement))
@returns(Statement)
def gen_S_try(ss, catches, fs):
  return Statement(C.S.TRY, b=ss, catches=catches, fs=fs)


# { (S... ) ; } => (S... ) ;
@takes(list_of(AST))
@returns(list_of(AST))
def rm_braces(nodes):
  if len(nodes) < 2: return nodes
  if nodes[0].getText() == '{' and nodes[-1].getText() == '}':
    return nodes[1:-1]
  else: return nodes


# parse a statement
# (STATEMENT ...)
@takes("Method", AST)
@returns(Statement)
def parse_s(mtd, node):
  curried_s = partial(parse_s, mtd)
  curried_e = lambda n: parse_e(n, mtd.clazz)

  _node = node.getChild(0)
  kind = _node.getText()
  _nodes = node.getChildren()

  # (S... (E... ) ';')
  if kind == C.T.EXP:
    _nodes = _node.getChildren()
    # (S... (E... var = (E... )) ';')
    if len(_nodes) >= 3 and _nodes[-2].getText() == '=':
      # var can be a list of nodes, e.g., x . y
      var_node = util.mk_v_node_w_children(_nodes[:-2])
      le = curried_e(var_node)
      re = curried_e(_nodes[-1])
      s = gen_S_assign(le, re)
    else: s = gen_S_e(curried_e(_node))

  # (S... assert (E... ) ';')
  elif kind == "assert":
    e = curried_e(node.getChild(1))
    s = gen_S_assert(e)

  # (S... return (E... )? ';')
  elif kind == "return":
    if node.getChildCount() > 2:
      s = gen_S_rtn(curried_e(node.getChild(1)))
    else:
      s = gen_S_rtn()

  # (S... if (E... ) { (S... ) } (else { (S... ) })?)
  elif kind == "if":
    e = curried_e(node.getChild(1))
    ss = _nodes[2:] # exclude first two nodes: S... if
    i = next((i for i, n in enumerate(ss) if n.getText() == "else"), -1)
    if i == -1: # no else branch
      t_s = rm_braces(ss)
      f_s = []
    else:
      t_s = rm_braces(ss[:i])
      f_s = rm_braces(ss[i+1:])
    ts = map(curried_s, t_s)
    fs = map(curried_s, f_s)
    s = gen_S_if(e, ts, fs)

  # (S... while (E... ) { (S... ) })
  elif kind == "while":
    e = curried_e(node.getChild(1))
    ss = _nodes[2:] # exclude first two nodes: while (E... )
    b = map(curried_s, rm_braces(ss))
    s = gen_S_while(e, b)

  # (S... repeat (E... ) { (S... ) })
  elif kind == "repeat":
    e = curried_e(node.getChild(1))
    ss = _nodes[2:] # exclude first two nodes: repeat (E... )
    b = map(curried_s, rm_braces(ss))
    s = gen_S_repeat(e, b)

  # (S... for (FOR_CTRL typ var : (E... ) ) { (S... ) })
  elif kind == "for":
    ctrl = node.getChild(1)
    ty = ctrl.getChild(0).getText()
    i = ctrl.getChild(1).getText()
    mtd.locals[i] = ty # NOTE: incorrect scope, in fact.
    e_def = gen_E_id(i, ty)
    e_iter = curried_e(ctrl.getChildren()[-1])
    ss = _nodes[2:] # exclude first two nodes: for (... )
    b = map(curried_s, rm_braces(ss))
    s = gen_S_for(e_def, e_iter, b)

  # (S... try { (S... ) }
  #   (catch (PARAMS typ var) { (S... ) })*
  #   (finally { (S... ) })? )
  elif kind == "try":
    catches = []
    fs = []

    idx = -1
    while abs(idx) <= node.getChildCount():
      __node = node.getChild(idx)
      __kind = __node.getText()
      if __kind == "finally":
        fs = map(curried_s, rm_braces(__node.getChildren()))
      elif __kind == "catch":
        ty = __node.getChild(0).getChild(0).getText()
        ex = __node.getChild(0).getChild(1).getText()
        e = gen_E_id(ex, ty)
        cs = map(curried_s, rm_braces(__node.getChildren()[1:]))
        catches.append( (e, cs) )
      elif __kind == '}': break
      idx = idx - 1

    b = map(curried_s, rm_braces(_nodes[1:idx+1]))
    s = gen_S_try(b, catches, fs)

  # (S... typ var (= (E... )) ';')
  elif node.getChildren()[-2].getText() == '=':
    var = node.getChildren()[-3].getText()
    # type can be a list of nodes, e.g., List < T >
    ty_node = util.mk_v_node_w_children(node.getChildren()[:-3])
    ty = util.implode_id(ty_node)
    mtd.locals[var] = ty
    le = gen_E_id(var, ty)
    re = curried_e(node.getChildren()[-2].getChild(0))
    s = gen_S_assign(le, re)

  # (DECL typ var ';') # local variable declaration
  elif node.getText() == C.T.DECL:
    ty = node.getChild(0).getText()
    var = node.getChild(1).getText()
    mtd.locals[var] = ty
    e_decl = gen_E_id(var, ty)
    s = gen_S_e(e_decl)

  else: raise Exception("unhandled statement", node.toStringTree())

  return s


# parse a method body 
# body ::= ';' | { s* }
@takes("Method", list_of(AST))
@returns(list_of(Statement))
def parse(mtd, nodes):
  if len(nodes) < 1: raise SyntaxError
  if len(nodes) == 1 and nodes[0].toStringTree() == ';': return []
  return map(partial(parse_s, mtd), rm_braces(nodes))


# parse and generate statements
@takes("Method", unicode)
@returns(list_of(Statement))
def to_statements(mtd, s):
  s_stream = antlr3.StringStream('{' + s + '}')
  lexer = Lexer(s_stream)
  t_stream = antlr3.CommonTokenStream(lexer)
  parser = Parser(t_stream)
  try:
    ast = parser.block()
    return parse(mtd, ast.tree.getChildren())
  except antlr3.RecognitionException:
    traceback.print_stack()
