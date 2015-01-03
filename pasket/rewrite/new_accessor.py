import copy as cp
import operator as op
from itertools import product
from functools import partial
import logging

import lib.const as C
import lib.visit as v

from .. import add_artifacts
from .. import util
from .. import sample
from ..meta.template import Template
from ..meta.clazz import Clazz
from ..meta.method import Method
from ..meta.field import Field
from ..meta.statement import Statement, to_statements
from ..meta.expression import Expression, to_expression, gen_E_gen

def to_shorty(ty):
  if ty == C.J.i: return u'i'
  elif ty == C.J.z: return u'b'
  elif ty == C.J.STR: return u's'
  else: return u""


class NewAccessor(object):
  # to build unique aux class names
  __cnt = 0

  aux_name = ""

  aux = Clazz()

  @staticmethod
  def get_aux():
    return NewAccessor.aux
  
  @staticmethod
  def set_aux(c):
    NewAccessor.aux = c
  
  @classmethod
  def new_aux(cls):
    cls.__cnt = cls.__cnt + 1
    return u"{}{}".format(C.ACC.AUX, cls.__cnt)

  def __init__(self, smpls):
    self._smpls = smpls
    self._cur_cls = None
    self._cur_mtd = None
    self._clss = []

  @v.on("node")
  def visit(self, node):
    """
    This is the generic method to initialize the dynamic dispatcher
    """

  # find possible classes for Getter/Setter
  # so as assume all classes
  def find_clss_involved(self, tmpl):
    for cls in util.flatten_classes(tmpl.classes, "inners"):
      # ignore interface
      if cls.is_itf and not cls.subs:
        logging.debug("ignore interface {}".format(cls.name))
        continue
      self._clss.append(cls)

  @staticmethod
  def is_candidate_cls(cls):
    mtds = cls.mtds
    getter_mtds = filter(lambda m: len(m.params) == 0 and m.typ != C.J.v, mtds)
    cons_mtds = filter(lambda m: m.is_init, mtds)
    return cls.is_class and (any(getter_mtds) or any(cons_mtds))

  # assume methods that participate will be neither <init> nor static
  @staticmethod
  def is_candidate_mtd(mtd):
    return not mtd.is_init and not mtd.is_static

  # retrieve candidate methods (in general)
  @staticmethod
  def get_candidate_mtds(cls):
    mtds = cls.mtds
    if cls.is_itf and cls.subs:
      mtds = util.flatten(map(op.attrgetter("mtds"), cls.subs))
    return filter(NewAccessor.is_candidate_mtd, mtds)

  # retrieve constructors
  @staticmethod
  def get_candidate_inits(cls):
    return filter(lambda x: x.is_init, cls.mtds)

  # add a global counter
  @staticmethod
  def add_global_counter(aux, fname):
    z = to_expression(u"0")
    d = Field(clazz=aux, mods=C.PRST, typ=C.J.i, name=fname, init=z)
    aux.add_flds([d])
    return d

  # restrict call stack for the given method via a global counter
  @staticmethod
  def limit_depth(aux, mtd, depth):
    fname = mtd.name + "_depth"
    NewAccessor.add_global_counter(aux, fname)
    prologue = to_statements(mtd, u"""
      if ({fname} > {depth}) return;
      {fname} = {fname} + 1;
    """.format(**locals()))
    epilogue = to_statements(mtd, u"""
      {fname} = {fname} - 1;
    """.format(**locals()))
    mtd.body = prologue + mtd.body + epilogue

  # restrict the number of the method invocations
  @staticmethod
  def limit_number(mtd, fld, cnt, rval=u''):
    fname = fld.name
    guard = to_statements(mtd, u"""
      if ({fname} > {cnt}) return {rval};
      {fname} = {fname} + 1;
    """.format(**locals()))
    mtd.body = guard + mtd.body
  
  # common params for getter methods (and part of setter methods)
  @staticmethod
  def getter_params():
    return [ (C.J.i, u"mtd_id"), (C.J.OBJ, u"callee"), (C.J.i, u"fld_id") ]

  # code for getting a field
  @staticmethod
  def __getter(aux, ty):
    shorty = to_shorty(ty)
    params = NewAccessor.getter_params()
    getr = Method(clazz=aux, mods=C.PBST, typ=ty, params=params, name=shorty+u"get")
    rtn = u"return callee._prvt_{}fld[fld_id];".format(shorty)
    getr.body = to_statements(getr, rtn)
    aux.add_mtds([getr])
    setattr(aux, shorty + "gttr", getr)

  @staticmethod
  def getter(aux):
    NewAccessor.__getter(aux, C.J.OBJ)
  
  @staticmethod
  def igetter(aux):
    NewAccessor.__getter(aux, C.J.i)
  
  @staticmethod
  def bgetter(aux):
    NewAccessor.__getter(aux, C.J.z)
  
  @staticmethod
  def sgetter(aux):
    NewAccessor.__getter(aux, C.J.STR)
  
  # code for setting a field
  @staticmethod
  def __setter(aux, ty):
    shorty = to_shorty(ty)
    params = NewAccessor.getter_params() + [(ty, u"val")]
    setr = Method(clazz=aux, mods=C.PBST, params=params, name=shorty+u"set")
    assign = u"callee._prvt_{}fld[fld_id] = val;".format(shorty)
    setr.body = to_statements(setr, assign)
    aux.add_mtds([setr])
    setattr(aux, shorty + "sttr", setr)

  @staticmethod
  def setter(aux):
    NewAccessor.__setter(aux, C.J.OBJ)
  
  @staticmethod
  def isetter(aux):
    NewAccessor.__setter(aux, C.J.i)
  
  @staticmethod
  def bsetter(aux):
    NewAccessor.__setter(aux, C.J.z)
  
  @staticmethod
  def ssetter(aux):
    NewAccessor.__setter(aux, C.J.STR)
 
  @staticmethod
  def check_getter_param(aux, nums, c):
    rule = Method(clazz=aux, mods=[C.mod.ST, C.mod.HN], name=u"check"+c+u"GetterParam")
    def check_single_getter(n):
      return u"assert 0 == (argNum(" + getattr(aux, '_'.join([C.ACC.GET, c, str(n)])) + "));"
    rule.body = to_statements(rule, "\n".join(map(check_single_getter, range(nums[c][1]))))
    aux.add_mtds([rule])
  
  @staticmethod
  def check_setter_param(aux, nums, c):
    rule = Method(clazz=aux, mods=[C.mod.ST, C.mod.HN], name=u"check"+c+u"SetterParam")
    def check_single_setter(n):
      return u"assert 1 == (argNum(" + getattr(aux, '_'.join([C.ACC.SET, c, str(n)])) + "));"
    if (nums[c][2] > 0):
      rule.body = to_statements(rule, "\n".join(map(check_single_setter, range(nums[c][2]))))
      aux.add_mtds([rule])

  # getter will be invoked here
  @staticmethod
  def __getter_in_one(aux, nums, fld_g, g_cnt, ty, default):
    shorty = to_shorty(ty)
    params = [(C.J.i, u"mtd_id"), (C.J.OBJ, u"callee")]
    one = Method(clazz=aux, mods=C.PBST, typ=ty, params=params, name=shorty+u"getterInOne")
    def getter_switch_whole(cl):
      def getter_switch(role):
        aname = aux.name
        v = getattr(aux, '_'.join([C.ACC.GET, cl, role]))
        f = getattr(aux, shorty + "gttr").name
        argpairs = params+[(C.J.i, getattr(aux, '_'.join([C.ACC.GS, cl, role])))]
        args = ", ".join(map(lambda (ty, nm): nm, argpairs))
        return u"if (mtd_id == {v}) return {aname}.{f}({args});".format(**locals())
      roles = map(str, range(nums[cl][1]))
      return "\nelse ".join(map(getter_switch, roles))
    one.body = to_statements(one, "\nelse ".join(map(getter_switch_whole, filter(lambda x: nums[x][1] > 0, nums.iterkeys()))))
    NewAccessor.limit_number(one, fld_g, g_cnt, default)
    aux.add_mtds([one])

  @staticmethod
  def getter_in_one(aux, nums, fld_g, g_cnt):
    NewAccessor.__getter_in_one(aux, nums, fld_g, g_cnt, C.J.OBJ, C.J.N)
  
  @staticmethod
  def igetter_in_one(aux, nums, fld_g, g_cnt):
    NewAccessor.__getter_in_one(aux, nums, fld_g, g_cnt, C.J.i, u"-1")
  
  @staticmethod
  def bgetter_in_one(aux, nums, fld_g, g_cnt):
    NewAccessor.__getter_in_one(aux, nums, fld_g, g_cnt, C.J.z, C.J.F)
  
  @staticmethod
  def sgetter_in_one(aux, nums, fld_g, g_cnt):
    NewAccessor.__getter_in_one(aux, nums, fld_g, g_cnt, C.J.STR, C.J.F)
  
  # setter will be invoked here
  @staticmethod
  def __setter_in_one(aux, nums, fld_s, s_cnt, ty):
    shorty = to_shorty(ty)
    params = [(C.J.i, u"mtd_id"), (C.J.OBJ, u"callee"), (ty, u"val")]
    one = Method(clazz=aux, mods=C.PBST, params=params, name=shorty+u"setterInOne")
    def setter_switch_whole(cl):
      def setter_switch(role):
        aname = aux.name
        v = getattr(aux, '_'.join([C.ACC.SET, cl, role]))
        f = getattr(aux, shorty + "sttr").name
        argpairs = [(C.J.i, u"mtd_id"), (C.J.OBJ, u"callee"), (C.J.i, getattr(aux, '_'.join([C.ACC.GS, cl, role]))), (ty, u"val")]
        args = ", ".join(map(lambda (ty, nm): nm, argpairs))
        return u"if (mtd_id == {v}) {aname}.{f}({args});".format(**locals())
      roles = map(str, range(nums[cl][2]))
      return "\nelse ".join(map(setter_switch, roles))
    one.body = to_statements(one, "\nelse ".join(map(setter_switch_whole, filter(lambda x: nums[x][2] > 0, nums.iterkeys()))))
    NewAccessor.limit_number(one, fld_s, s_cnt)
    aux.add_mtds([one])

  @staticmethod
  def setter_in_one(aux, nums, fld_s, s_cnt):
    NewAccessor.__setter_in_one(aux, nums, fld_s, s_cnt, C.J.OBJ)
  
  @staticmethod
  def isetter_in_one(aux, nums, fld_s, s_cnt):
    NewAccessor.__setter_in_one(aux, nums, fld_s, s_cnt, C.J.i)
  
  @staticmethod
  def bsetter_in_one(aux, nums, fld_s, s_cnt):
    NewAccessor.__setter_in_one(aux, nums, fld_s, s_cnt, C.J.z)
  
  @staticmethod
  def ssetter_in_one(aux, nums, fld_s, s_cnt):
    NewAccessor.__setter_in_one(aux, nums, fld_s, s_cnt, C.J.STR)

  # initializer will be invoked here
  @staticmethod
  def __constructor_in_one(aux, nums, fld_c, c_cnt, ty):
    shorty = to_shorty(ty)
    params = [(C.J.i, u"mtd_id"), (C.J.OBJ, u"callee"), (ty, u"val"), (C.J.i, u"fld_id")]
    one = Method(clazz=aux, mods=C.PBST, params=params, name=shorty+u"constructorInOne")
    def constructor_switch_whole(cl):
      aname = aux.name
      v = getattr(aux, '_'.join([C.ACC.CONS, cl]))
      f = getattr(aux, shorty + "sttr").name
      argpairs = NewAccessor.getter_params() + [(ty, u"val")]
      args = ", ".join(map(lambda (ty, nm): nm, argpairs))
      return u"if (mtd_id == {v}) {aname}.{f}({args});".format(**locals())
    one.body = to_statements(one, "\nelse ".join(map(constructor_switch_whole, filter(lambda n: nums[n][0]>=0, nums.iterkeys()))))
    NewAccessor.limit_number(one, fld_c, c_cnt)
    aux.add_mtds([one])

  @staticmethod
  def constructor_in_one(aux, nums, fld_c, c_cnt):
    NewAccessor.__constructor_in_one(aux, nums, fld_c, c_cnt, C.J.OBJ)
  
  @staticmethod
  def iconstructor_in_one(aux, nums, fld_c, c_cnt):
    NewAccessor.__constructor_in_one(aux, nums, fld_c, c_cnt, C.J.i)

  @staticmethod
  def bconstructor_in_one(aux, nums, fld_c, c_cnt):
    NewAccessor.__constructor_in_one(aux, nums, fld_c, c_cnt, C.J.z)

  @staticmethod
  def sconstructor_in_one(aux, nums, fld_c, c_cnt):
    NewAccessor.__constructor_in_one(aux, nums, fld_c, c_cnt, C.J.STR)
  
  @staticmethod
  def add_fld(cls, ty, nm):
    logging.debug("adding field {}.{} of type {}".format(cls.name, nm, ty))
    fld = Field(clazz=cls, typ=ty, name=nm)
    cls.add_flds([fld])
    cls.init_fld(fld)
    return fld

  # a method that calls the adaptee
  @staticmethod
  def call_adaptee(aux, clss):
    callee = u'_'.join(["rcv", aux.name])
    rcv = u"_prvt_fld[" + getattr(aux, C.ACC.FLD) + u"]"
    params = [(C.J.i, u"mtd_id"), (aux.name, callee)]
    reflect = Method(clazz=aux, mods=C.PBST, params=params, name=u"call_adaptee")
    def switch( cls ):
      mtds = NewAccessor.get_candidate_mtds(cls)
      def invoke(mtd):
        cls = mtd.clazz
        # if there is no implementer for this method in interface, ignore it
        if cls.is_itf and not cls.subs: return u''
        if len(mtd.params) != 0 or mtd.typ != C.J.v: return u''
        call = "if ({} != null) {}.{}.{}();".format(callee, callee, rcv, mtd.name)
        adaptee_id = getattr(aux, "adaptee")
        return u"if ({adaptee_id} == {mtd.id}) {{ {call} }}".format(**locals())
      invocations = filter(None, map(invoke, mtds))
      return "\nelse ".join(invocations)
    tests = filter(None, map(switch, clss))
    prefix = u"if (" + getattr(aux, C.ACC.ADPT) + u" == mtd_id) {\n"
    reflect.body = to_statements(reflect, prefix + u"\nelse ".join(tests) + u"\n}")
    NewAccessor.limit_depth(aux, reflect, 2)
    aux.add_mtds([reflect])
    setattr(aux, "call_adaptee", reflect)

  ##
  ## generate an aux type for getter/setter
  ##
  def gen_aux_cls(self, nums, tmpl):
    self.aux_name = NewAccessor.new_aux()
    tmpl.acc_auxs.append(self.aux_name)
    aux = Clazz(name=self.aux_name, mods=[C.mod.PB], subs=self._clss)
    NewAccessor.set_aux(aux)

    rv_cons = []
    for c in nums:
      rv_cons.append('_'.join([C.ACC.CONS, c]))
    #constructor_args = []
    #for c in nums:
    #  new_args = map(lambda n: '_'.join([C.ACC.CONS, c, str(n)]), range(nums[c][0]))
    #  constructor_args.extend(new_args)
    
    rv_accs = map(lambda c: '_'.join([C.ACC.ACC, c]), nums.iterkeys())

    def get_g_roles(name, c):
      return map(lambda n: '_'.join([name, c, str(n)]), range(nums[c][1]))
    def get_s_roles(name, c):
      return map(lambda n: '_'.join([name, c, str(n)]), range(nums[c][2]))

    rv_gtts = util.flatten(map(partial(get_g_roles, C.ACC.GET), nums.iterkeys()))
    gs_vars = util.flatten(map(partial(get_g_roles, C.ACC.GS), nums.iterkeys()))
    rv_stts = util.flatten(map(partial(get_s_roles, C.ACC.SET), nums.iterkeys()))

    # set role variables
    def set_role(role):
      setattr(aux, role, '_'.join([role, aux.name]))
    map(set_role, rv_cons)
    #map(set_role, constructor_args)
    map(set_role, rv_accs)
    map(set_role, rv_gtts)
    map(set_role, gs_vars)
    map(set_role, rv_stts)
    
    # add fields that stand for non-deterministic rule choices
    def aux_fld(init, ty, nm):
      if hasattr(aux, nm): nm = getattr(aux, nm)
      return Field(clazz=aux, mods=[C.mod.ST], typ=ty, name=nm, init=init)
    hole = to_expression(C.T.HOLE)
    aux_int = partial(aux_fld, hole, C.J.i)

    aux.add_flds(map(aux_int, gs_vars))

    c_to_e = lambda c: to_expression(unicode(c))

    ## range check
    rg_chk = Method(clazz=aux, mods=[C.mod.ST, C.mod.HN], name=u"checkRange")
    checkers = []
    gen_range = lambda ids: gen_E_gen(map(c_to_e, ids))
    get_id = op.attrgetter("id")

    # range check for accessors
    cls_ids = map(get_id, filter(NewAccessor.is_candidate_cls, self._clss))
    cls_init = gen_range(cls_ids)
    aux_int_cls = partial(aux_fld, cls_init, C.J.i)
    aux.add_flds(map(aux_int_cls, rv_accs))

    # range check for getter/setter
    mtds = util.flatten(map(NewAccessor.get_candidate_mtds, self._clss))

    def aux_range(rl, c, nm, mtds, num_args, is_void):
      ids = map(get_id, filter(lambda m: len(m.params) == num_args and (m.typ == C.J.v) == is_void, mtds))
      init = gen_range(ids)
      role = getattr(aux, '_'.join(map(str, [rl, c, nm])))
      aux.add_flds([aux_fld(init, C.J.i, role)])

    def mtd_range(c):
      map(lambda m: [aux_range(C.ACC.GET, c, m, mtds, 0, False)], range(nums[c][1]))
      map(lambda m: [aux_range(C.ACC.SET, c, m, mtds, 1, True)], range(nums[c][2]))
    map(mtd_range, nums.iterkeys())

    # range check for constructors
    inits = util.flatten(map(NewAccessor.get_candidate_inits, self._clss))
    cons_ids = map(get_id, inits)
    cons_init = gen_range(cons_ids)
    aux_int_cons = partial(aux_fld, cons_init, C.J.i)
    aux.add_flds(map(aux_int_cons, rv_cons))

    for c in nums.iterkeys():
      if nums[c][0] >= 0:
        checkers.append("assert (argNum(" + getattr(aux, '_'.join([C.ACC.CONS, c])) + ")) == " + str(nums[c][0]) + ";")
        checkers.append("assert (belongsTo(" + getattr(aux, '_'.join([C.ACC.CONS, c])) + ")) == " + getattr(aux, '_'.join([C.ACC.ACC, c])) + ";")
    
    def owner_range(rl, c, ids):
      return map(lambda i: "assert subcls("+getattr(aux, '_'.join([C.ACC.ACC, c]))+", belongsTo("+getattr(aux, '_'.join([rl, c, str(i)]))+"));", ids)
    for c in nums.iterkeys():
      checkers.extend(owner_range(C.ACC.GET, c, range(nums[c][1])))
      checkers.extend(owner_range(C.ACC.SET, c, range(nums[c][2])))

    def bundle_getter_setter(c, gids, sids):
      return map(lambda (g, s): "assert belongsTo("+getattr(aux, '_'.join([C.ACC.GET, c, str(g)])) + ") == belongsTo(" + getattr(aux, '_'.join([C.ACC.SET, c, str(s)])) + ");", product(gids, sids))
    for c in nums.iterkeys():
      checkers.extend(bundle_getter_setter(c, range(nums[c][1]), range(nums[c][2])))

    def getter_sig(c):
      return map(lambda i: "assert (argNum(" + getattr(aux, '_'.join([C.ACC.GET, c, str(i)])) + ")) == 0;", range(nums[c][1]))
    def setter_sig(c):
      return map(lambda i: "assert (argNum(" + getattr(aux, '_'.join([C.ACC.SET, c, str(i)])) + ")) == 1;", range(nums[c][2]))
    def gs_match(c):
      return map(lambda i: "assert subcls(argType(" + getattr(aux, '_'.join([C.ACC.SET, c, str(i)]))+", 0), retType(" + getattr(aux, '_'.join([C.ACC.GET, c, str(i)])) + "));", range(nums[c][2]))
    def gs_type_match(c):
        return map(lambda i: "assert argType(" + getattr(aux, '_'.join([C.ACC.CONS, c])) + ", " + getattr(aux, '_'.join([C.ACC.GS, c, str(i)]))+") == retType(" + getattr(aux, '_'.join([C.ACC.GET, c, str(i)]))+");", range(nums[c][1]))
    checkers.extend(reduce(lambda x,y: x+y, map(getter_sig, nums.iterkeys())))
    checkers.extend(reduce(lambda x,y: x+y, map(setter_sig, nums.iterkeys())))
    checkers.extend(reduce(lambda x,y: x+y, map(gs_match, nums.iterkeys())))
    checkers.extend(reduce(lambda x,y: x+y, map(gs_type_match, nums.iterkeys())))

    ## adapter pattern

    map(set_role, C.adp_roles)
    mtd_ids = map(get_id, mtds)
    mtd_init = gen_range(mtd_ids)
    aux_int_adap = partial(aux_fld, mtd_init, C.J.i)
    adapter_roles = [C.ACC.ADPT, C.ACC.ADPE]
    adapter_flds = map(aux_int_adap, adapter_roles)
    aux.add_flds(adapter_flds + [aux_int(C.ACC.FLD)])
    NewAccessor.call_adaptee(aux, self._clss)

    #checkers.append(u"assert (argNum(" + getattr(aux, C.ACC.ADPT) + ")) == 0 && (argNum(" + getattr(aux, C.ACC.ADPT) + ")) == 0;")
    #checkers.append(u"assert (retType(" + getattr(aux, C.ACC.ADPT) + ")) == -1 && (retType(" + getattr(aux, C.ACC.FLD) + ")) == -1;")

    rg_chk.body += to_statements(rg_chk, '\n'.join(checkers))
    aux.add_mtds([rg_chk])
    
    for c in nums.iterkeys():
      if nums[c][1] > 0:
        NewAccessor.check_getter_param(aux, nums, c)
      if nums[c][2] > 0:
        NewAccessor.check_setter_param(aux, nums, c)

    ## global counters

    # assumption: # of objects and events could be instantiated
    obj_cnt = sample.max_objs(self._smpls)
    evt_cnt = sample.max_evts(self._smpls)
    arg_cnt = sum(map(lambda (c, g, s): c, C.acc_conf.values()))
    # +1 : counting InvocationEvent
    c_cnt = (obj_cnt + evt_cnt + 1) * (arg_cnt + 1) / len(C.acc_conf.keys())

    # assumption: each getter could be invoked per event
    g_cnt = sum(map(lambda (c, g, s): g, C.acc_conf.values()))
    g_cnt = g_cnt * evt_cnt

    # assumption: setter could be invoked once, excluding constructor
    s_cnt = sum(map(lambda (c, g, s): s, C.acc_conf.values()))
    s_cnt = s_cnt * evt_cnt
    s_cnt = s_cnt + c_cnt # as constructor can call setter

    # getter pattern

    NewAccessor.getter(aux)
    NewAccessor.igetter(aux)
    NewAccessor.bgetter(aux)
    NewAccessor.sgetter(aux)

    fld_g = NewAccessor.add_global_counter(aux, u"getter_cnt")

    NewAccessor.getter_in_one(aux, nums, fld_g, g_cnt)
    NewAccessor.igetter_in_one(aux, nums, fld_g, g_cnt)
    NewAccessor.bgetter_in_one(aux, nums, fld_g, g_cnt)
    NewAccessor.sgetter_in_one(aux, nums, fld_g, g_cnt)

    # setter pattern

    NewAccessor.setter(aux)
    NewAccessor.isetter(aux)
    NewAccessor.bsetter(aux)
    NewAccessor.ssetter(aux)

    fld_s = NewAccessor.add_global_counter(aux, u"setter_cnt")

    NewAccessor.setter_in_one(aux, nums, fld_s, s_cnt)
    NewAccessor.isetter_in_one(aux, nums, fld_s, s_cnt)
    NewAccessor.bsetter_in_one(aux, nums, fld_s, s_cnt)
    NewAccessor.ssetter_in_one(aux, nums, fld_s, s_cnt)

    # constructor pattern

    fld_c = NewAccessor.add_global_counter(aux, "constructor_cnt")

    NewAccessor.constructor_in_one(aux, nums, fld_c, c_cnt)
    NewAccessor.iconstructor_in_one(aux, nums, fld_c, c_cnt)
    NewAccessor.bconstructor_in_one(aux, nums, fld_c, c_cnt)
    NewAccessor.sconstructor_in_one(aux, nums, fld_c, c_cnt)

    add_artifacts([aux.name])
    return aux


  @v.when(Template)
  def visit(self, node):
    nums = C.acc_conf
    
    self.find_clss_involved(node)
    aux = self.gen_aux_cls(nums, node)
    node.add_classes([aux])

  @v.when(Clazz)
  def visit(self, node):
    if (node.name == C.J.OBJ):
      def add_private_fld(n):
        NewAccessor.add_fld(node, NewAccessor.get_aux().name+u"[]", u"_prvt_fld")
        NewAccessor.add_fld(node, C.J.i+u"[]", u"_prvt_ifld")
        NewAccessor.add_fld(node, C.J.z+u"[]", u"_prvt_bfld")
        NewAccessor.add_fld(node, C.J.STR+u"[]", u"_prvt_sfld")
      map(add_private_fld, range(1))
    self._cur_cls = node

  @v.when(Field)
  def visit(self, node): pass

  @v.when(Method)
  def visit(self, node):
    self._cur_mtd = node

    if node.clazz.name in C.acc_default: return

    if node.clazz.client: return
 
    # constructors
    if node.is_init:
      for i in xrange(len(node.params)):
        shorty = to_shorty(node.params[i][0])
        mname = shorty + u"constructorInOne"
        fid = unicode(i)
        args = ", ".join([unicode(node.id), C.J.THIS, unicode(node.params[i][1]), fid])
        node.body += to_statements(node, u"{}.{}({});".format(self.aux_name, mname, args))
      return
    
    # getter candidate
    if len(node.params) == 0 and node.typ != C.J.v:
      shorty = to_shorty(node.typ)
      mname = shorty + u"getterInOne"
      callee = u"null" if node.is_static else C.J.THIS
      node.body += to_statements(node, u"return " + self.aux_name + u"." + mname + u"(" + unicode(node.id) + u", " + callee + u");")

    # setter candidate
    if len(node.params) == 1 and node.typ == C.J.v:
      shorty = to_shorty(node.params[0][0])
      mname = shorty + u"setterInOne"
      callee = u"null" if node.is_static else C.J.THIS
      node.body += to_statements(node, self.aux_name + u"." + mname + u"(" + unicode(node.id) + u", " + callee + u", " + unicode(node.params[0][1]) + u");")

    # adapter candidate
    if len(node.params) == 0 and node.typ == C.J.v and not node.is_static:
      aux = NewAccessor.get_aux()
      #fname = u"_prvt_fld"
      #callee = C.J.THIS+u"."+fname+u"["+getattr(aux, C.ACC.ADPT)+u"]"
      node.body += to_statements(node, self.aux_name + u".call_adaptee(" + unicode(node.id) + u", " + unicode(C.J.THIS) + u");")

  @v.when(Statement)
  def visit(self, node): return [node]

  @v.when(Expression)
  def visit(self, node): return node

