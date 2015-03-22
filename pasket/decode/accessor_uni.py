from functools import partial
import re
import logging
from sets import Set

import lib.visit as v
import lib.const as C

from .. import util
from ..meta import methods, classes, class_lookup
from ..meta.template import Template
from ..meta.clazz import Clazz
from ..meta.method import Method, find_formals
from ..meta.field import Field
from ..meta.statement import Statement, to_statements
from ..meta.expression import Expression

class AccessorUni(object):

  __aux_name = C.ACC.AUX+"Uni"

  regex_log = r"log::check_log::(-)(\d+)"
  
  _invoked = Set()

  @staticmethod
  def is_method_log(msg):
    return re.match(AccessorUni.regex_log, msg)

  def add_invoked(self, msg):
    m = re.match(AccessorUni.regex_log, msg)
    self._invoked.add(int(m.group(2)))

  ## hole assignments for roles
  ## glblInit_accessor_????,StmtAssign,accessor_???? = n
  regex_role = r"(({})_\S+_\d+_{}).* = (\d+)$".format('|'.join(C.acc_roles), __aux_name)

  @staticmethod
  def simple_role_of_interest(msg):
    return re.match(AccessorUni.regex_role, msg)

  # add a mapping from role variable to its value chosen by sketch
  def add_simple_role(self, msg):
    m = re.match(AccessorUni.regex_role, msg)
    v, n = m.group(1), m.group(3)
    self._role[v] = n

  # initializer
  def __init__(self, cmd, output_path, acc_conf):
    self._cmd = cmd
    self._output = output_path
    self._demo = util.pure_base(output_path)
    self._acc_conf = acc_conf

    self._cur_mtd = None
    self._role = {} # { v : n }

    # class roles
    self._accessors = {} # { Aux... : {key1 : accessor1, key2 : accessor2} }

    # method roles
    self._getters = {} # { Aux... : {key1 : getter1, key2 : getter2 ...} }
    self._setters = {} # { Aux... : {key1 : setter1, key2 : setter2 ...} }
    self._cons = {} # { Aux... : {key1 : cons1, key2 : cons2 ...} }

    # getter/setter fields
    self._gs = {}
    
    # interpret the synthesis result
    with open(self._output, 'r') as f:
      for line in f:
        line = line.strip()
        try:
          if AccessorUni.is_method_log(line): self.add_invoked(line)
          items = line.split(',')
          func, kind, msg = items[0], items[1], ','.join(items[2:])
          if AccessorUni.simple_role_of_interest(msg): self.add_simple_role(msg)
        except IndexError: # not a line generated by custom codegen
          pass # if "Total time" in line: logging.info(line)

  @property
  def demo(self):
    return self._demo

  @v.on("node")
  def visit(self, node):
    """
    This is the generic method to initialize the dynamic dispatcher
    """

  # add a private field
  @staticmethod
  def add_prvt_fld(acc, inst, typ, num):
    name = u'_'.join([C.ACC.prvt, unicode(num), acc.name])
    fld = acc.fld_by_name(name)
    if not fld:
      logging.debug("adding private field {} for {} of type {}".format(name, acc.name, typ))
      fld = Field(clazz=acc, typ=typ, name=name)
      acc.add_fld(fld)
    setattr(acc, C.ACC.CONS+"_"+inst+"_"+str(num), fld)

  # getter code
  @staticmethod
  def def_getter(mtd, acc, fld_id):
    logging.debug("adding getter code into {}".format(repr(mtd)))
    get = u"return {}_{}_{};".format(C.ACC.prvt, unicode(fld_id), acc.name)
    mtd.body = to_statements(mtd, get)

  # setter code
  @staticmethod
  def def_setter(mtd, acc, fld_id, typ):
    args = find_formals(mtd.params, [typ])
    logging.debug("adding setter code into {}".format(repr(mtd)))
    set = u"{}_{}_{} = {};".format(C.ACC.prvt, unicode(fld_id), acc.name, args[0])
    mtd.body = to_statements(mtd, set)

  # constructor code
  @staticmethod
  def def_constructor(mtd, acc):
    logging.debug("adding constructor code into {}".format(repr(mtd)))
    for i, (ty, nm) in enumerate(mtd.params):
      init = u"{}_{}_{} = {};".format(C.ACC.prvt, unicode(i), acc.name, nm)
      mtd.body += to_statements(mtd, init)

  @v.when(Template)
  def visit(self, node):
    def find_role(lst, aux_name, role):
      try:
        _id = self._role['_'.join([role, aux_name])]
        return lst[int(_id)]
      except KeyError: return None

    aux_name = self.__aux_name
    aux = class_lookup(aux_name)

    # find and store class roles
    find_cls_role = partial(find_role, classes(), aux_name)

    # find and store method roles
    find_mtd_role = partial(find_role, methods(), aux_name)
      
    cons = {}
    for key in self._acc_conf.iterkeys():
      cons[key] = find_mtd_role('_'.join([C.ACC.CONS, key]))
    #cons_params = []
    #for key in self._acc_conf.iterkeys():
    #  if self._acc_conf[key][0] >= 0:
    #    cons_params += map(find_mtd_role, map(lambda x: '_'.join([C.ACC.CONS, key, x]), range(self._acc_conf[key][0])))
    getters = {}
    for key in self._acc_conf.iterkeys():
      getters[key] = {}
      for x in xrange(self._acc_conf[key][1]):
        getters[key][x] = find_mtd_role('_'.join([C.ACC.GET, key, str(x)]))
    setters = {}
    for key in self._acc_conf.iterkeys():
      setters[key] = {}
      for x in xrange(self._acc_conf[key][2]):
        setters[key][x] = find_mtd_role('_'.join([C.ACC.SET, key, str(x)]))
    gs = {}
    for key in self._acc_conf.iterkeys():
      gs[key] = {}
      for x in xrange(max(self._acc_conf[key][1], self._acc_conf[key][2])):
        gs[key][x] = self._role['_'.join([C.ACC.GS, key, str(x), aux_name])]

    self._cons[aux.name] = cons
    self._getters[aux.name] = getters
    self._setters[aux.name] = setters
    self._gs[aux.name] = gs

    # add private fields for constructors
    for k in cons.iterkeys():
      c = cons[k]
      if not c: continue
      if util.exists(lambda m: m.id in self._invoked, c.clazz.mtds):
        for n, t in enumerate(c.param_typs):
          AccessorUni.add_prvt_fld(c.clazz, k, t, n)
        AccessorUni.def_constructor(c, c.clazz)

    # add private fields for getters/setters
    # insert or move code snippets from Aux classes to actual participants
    for k in gs.iterkeys():
      for e in gs[k].iterkeys():
        getr = getters[k][e]
        setr = setters[k][e] if e in setters[k].keys() else None
        effective = False
        if self._cmd == "android": effective = True
        elif self._cmd == "gui":
          effective = getr.id in self._invoked
          if not effective: effective = setr and setr.id in self._invoked

        if effective:
          AccessorUni.add_prvt_fld(getr.clazz, k, getr.typ, int(gs[k][e]))
          logging.debug("getter: {}_{}: {}".format(k, e, repr(getr)))
          AccessorUni.def_getter(getr, getr.clazz, gs[k][e])

          if setr:
            AccessorUni.add_prvt_fld(setr.clazz, k, setr.param_typs[0], int(gs[k][e]))
            logging.debug("setter: {}_{}: {}".format(k, e, repr(setr)))
            AccessorUni.def_setter(setr, setr.clazz, gs[k][e], setr.param_typs[0])

    # remove Aux class
    node.classes.remove(aux)

  @v.when(Clazz)
  def visit(self, node): pass

  @v.when(Field)
  def visit(self, node): pass

  @v.when(Method)
  def visit(self, node):
    self._cur_mtd = node

  @v.when(Statement)
  def visit(self, node):
    if node.kind == C.S.EXP and node.e.kind == C.E.CALL:
      call = unicode(node)
      if call.startswith(C.ACC.AUX+"Uni"):
        logging.debug("removing {}".format(call))
        if "setterInOne" in call or "SetterInOne" in call:
          ## Aux.....setterInOne(...);
          return to_statements(self._cur_mtd, u"if (null != null) return;")
        else:
          ## Aux...constructor...
          return []
    if node.kind == C.S.RETURN:
      call = unicode(node)
      if call.startswith(u"return " + C.ACC.AUX+"Uni"):
        logging.debug("removing {}".format(call))
        if "iGetterInOne" in call:
        ## Aux.....iGetterInOne(...);
          return to_statements(self._cur_mtd, u"return 0;")
        elif "zGetterInOne" in call:
        ## Aux.....zGetterInOne(...);
          return to_statements(self._cur_mtd, u"return false;")
        elif "getterInOne" in call:
        ## Aux.....sGetterInOne(...);
          return to_statements(self._cur_mtd, u"return null;")

    return [node]

  @v.when(Expression)
  def visit(self, node): return node

