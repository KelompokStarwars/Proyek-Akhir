from __future__ import print_function
from collections import namedtuple
from itertools import count
import math
import multiprocessing
from multiprocessing.pool import Pool
import random
import re
import sys
import time

N =9
W = N + 2
empty = "\n".join([(N+1)*' '] + N*[' '+N*'.'] + [(N+2)*' '])
colstr = 'ABCDEFGHI'
#MAX_GAME_LEN = N * N 

N_SIMS = 800
RAVE_EQUIV = 3500
EXPAND_VISITS = 8
PRIOR_EVEN = 10  # should be even number; 0.5 prior
PRIOR_SELFATARI = 10  # negative prior
PRIOR_CAPTURE_ONE = 15
PRIOR_CAPTURE_MANY = 30
PRIOR_PAT3 = 10
PRIOR_LARGEPATTERN = 100  # most moves have relatively small probability
PRIOR_CFG = [24, 22, 8]  # priors for moves in cfg dist. 1, 2, 3
PRIOR_EMPTYAREA = 10
REPORT_PERIOD = 200
PROB_HEURISTIC = {'capture': 0.8, 'pat3': 0.9}  # probability of heuristic suggestions being taken in playout
PROB_SSAREJECT = 0.9  # probability of rejecting suggested self-atari in playout
PROB_RSAREJECT = 0.5  # probability of rejecting random self-atari in playout; this is lower than above to allow nakade
RESIGN_THRES = 0.2
FASTPLAY20_THRES = 0.8  # if at 20% playouts winrate is >this, stop reading
FASTPLAY5_THRES = 0.95  # if at 5% playouts winrate is >this, stop reading

pat3src = [  # 3x3 playout patterns; X,O are colors, x,o are their inverses
       ["XOX",  # hane pattern - enclosing hane
        "...",
        "???"],
       ["XO.",  # hane pattern - non-cutting hane
        "...",
        "?.?"],
       ["XO?",  # hane pattern - magari
        "X..",
        "x.?"],
        ["XOO",  # hane pattern - thin hane
         "...",
         "?.?"], 
       [".O.",  # generic pattern - katatsuke or diagonal attachment; similar to magari
        "X..",
        "..."],
       ["XO?",  # cut1 pattern (kiri] - unprotected cut
        "O.o",
        "?o?"],
       ["XO?",  # cut1 pattern (kiri] - peeped cut
        "O.X",
        "???"],
       ["?X?",  # cut2 pattern (de]
        "O.O",
        "ooo"],
       ["OX?",  # cut keima
        "o.O",
        "???"],
       ["X.?",  # side pattern - chase
        "O.?",
        "   "],
       ["OX?",  # side pattern - block side cut
        "X.O",
        "   "],
       ["?X?",  # side pattern - block side connection
        "x.O",
        "   "],
       ["?XO",  # side pattern - sagari
        "x.x",
        "   "],
       ["?OX",  # side pattern - cut
        "X.O",
        "   "],
       ]

pat_gridcular_seq = [  # Sequence of coordinate offsets of progressively wider diameters in gridcular metric
        [[0,0],
         [0,1], [0,-1], [1,0], [-1,0],
         [1,1], [-1,1], [1,-1], [-1,-1], ],  # d=1,2 is not considered separately
        [[0,2], [0,-2], [2,0], [-2,0], ],
        [[1,2], [-1,2], [1,-2], [-1,-2], [2,1], [-2,1], [2,-1], [-2,-1], ],
        [[0,3], [0,-3], [2,2], [-2,2], [2,-2], [-2,-2], [3,0], [-3,0], ],
        [[1,3], [-1,3], [1,-3], [-1,-3], [3,1], [-3,1], [3,-1], [-3,-1], ],
        [[0,4], [0,-4], [2,3], [-2,3], [2,-3], [-2,-3], [3,2], [-3,2], [3,-2], [-3,-2], [4,0], [-4,0], ],
        [[1,4], [-1,4], [1,-4], [-1,-4], [3,3], [-3,3], [3,-3], [-3,-3], [4,1], [-4,1], [4,-1], [-4,-1], ],
        [[0,5], [0,-5], [2,4], [-2,4], [2,-4], [-2,-4], [4,2], [-4,2], [4,-2], [-4,-2], [5,0], [-5,0], ],
        [[1,5], [-1,5], [1,-5], [-1,-5], [3,4], [-3,4], [3,-4], [-3,-4], [4,3], [-4,3], [4,-3], [-4,-3], [5,1], [-5,1], [5,-1], [-5,-1], ],
        [[0,6], [0,-6], [2,5], [-2,5], [2,-5], [-2,-5], [4,4], [-4,4], [4,-4], [-4,-4], [5,2], [-5,2], [5,-2], [-5,-2], [6,0], [-6,0], ],
        [[1,6], [-1,6], [1,-6], [-1,-6], [3,5], [-3,5], [3,-5], [-3,-5], [5,3], [-5,3], [5,-3], [-5,-3], [6,1], [-6,1], [6,-1], [-6,-1], ],
        [[0,7], [0,-7], [2,6], [-2,6], [2,-6], [-2,-6], [4,5], [-4,5], [4,-5], [-4,-5], [5,4], [-5,4], [5,-4], [-5,-4], [6,2], [-6,2], [6,-2], [-6,-2], [7,0], [-7,0], ],
    ]
spat_patterndict_file = 'patterns.spat'
large_patterns_file = 'patterns.prob'

def neighbors(c):
    """ generator of coordinates for all neighbors of c """
    return [c-1, c+1, c-W, c+W]

def diag_neighbors(c):
    """ generator of coordinates for all diagonal neighbors of c """
    return [c-W-1, c-W+1, c+W-1, c+W+1]


def board_put(board, c, p):
    return board[:c] + p + board[c+1:]


def floodfill(board, c):
    """ replace continuous-color area starting at c with special color # """
    # This is called so much that a bytearray is worthwhile...
    byteboard = bytearray(board)
    p = byteboard[c]
    byteboard[c] = ord('#')
    fringe = [c]
    while fringe:
        c = fringe.pop()
        for d in neighbors(c):
            if byteboard[d] == p:
                byteboard[d] = ord('#')
                fringe.append(d)
    return str(byteboard)

contact_res = dict()
for p in ['.', 'x', 'X']:
    rp = '\\.' if p == '.' else p
    contact_res_src = ['#' + rp,  # p at right
                       rp + '#',  # p at left
                       '#' + '.'*(W-1) + rp,  # p below
                       rp + '.'*(W-1) + '#']  # p above
    contact_res[p] = re.compile('|'.join(contact_res_src), flags=re.DOTALL)

def contact(board, p):
    m = contact_res[p].search(board)
    if not m:
        return None
    return m.start() if m.group(0)[0] == p else m.end() - 1


def is_eyeish(board, c):
    eyecolor = None
    for d in neighbors(c):
        if board[d].isspace():
            continue
        if board[d] == '.':
            return None
        if eyecolor is None:
            eyecolor = board[d]
            othercolor = eyecolor.swapcase()
        elif board[d] == othercolor:
            return None
    return eyecolor

def is_eye(board, c):
    eyecolor = is_eyeish(board, c)
    if eyecolor is None:
        return None

    falsecolor = eyecolor.swapcase()
    false_count = 0
    at_edge = False
    for d in diag_neighbors(c):
        if board[d].isspace():
            at_edge = True
        elif board[d] == falsecolor:
            false_count += 1
    if at_edge:
        false_count += 1
    if false_count >= 2:
        return None

    return eyecolor


class Position(namedtuple('Position', 'board cap n ko last last2 komi')):

    def move(self, c):
        if c == self.ko:
            return None
        
        in_enemy_eye = is_eyeish(self.board, c) == 'x'

        board = board_put(self.board, c, 'X')
       
        capX = self.cap[0]
        singlecaps = []
        for d in neighbors(c):
            if board[d] != 'x':
                continue    
            fboard = floodfill(board, d) 
            if contact(fboard, '.') is not None:
                continue  

            capcount = fboard.count('#')
            if capcount == 1:
                singlecaps.append(d)
            capX += capcount
            board = fboard.replace('#', '.')  
        
        ko = singlecaps[0] if in_enemy_eye and len(singlecaps) == 1 else None
       
        if contact(floodfill(board, c), '.') is None:
            return None

        
        return Position(board=board.swapcase(), cap=(self.cap[1], capX),
                        n=self.n + 1, ko=ko, last=c, last2=self.last, komi=self.komi)

    def pass_move(self):
        return Position(board=self.board.swapcase(), cap=(self.cap[1], self.cap[0]),
                        n=self.n + 1, ko=None, last=None, last2=self.last, komi=self.komi)

    def moves(self, i0):
        i = i0-1
        passes = 0
        while True:
            i = self.board.find('.', i+1)
            if passes > 0 and (i == -1 or i >= i0):
                break  # we have looked through the whole board
            elif i == -1:
                i = 0
                passes += 1
                continue  # go back and start from the beginning
            # Test for to-play player's one-point eye
            if is_eye(self.board, i) == 'X':
                continue
            yield i

    def last_moves_neighbors(self):
        """ generate a randomly shuffled list of points including and
        surrounding the last two moves (but with the last move having
        priority) """
        clist = []
        for c in self.last, self.last2:
            if c is None:  continue
            dlist = [c] + list(neighbors(c) + diag_neighbors(c))
            random.shuffle(dlist)
            clist += [d for d in dlist if d not in clist]
        return clist

    def score(self, owner_map=None):
        """ compute score for to-play player; this assumes a final position
        with all dead stones captured; if owner_map is passed, it is assumed
        to be an array of statistics with average owner at the end of the game
        (+1 black, -1 white) """
        board = self.board
        i = 0
        while True:
            i = self.board.find('.', i+1)
            if i == -1:
                break
            fboard = floodfill(board, i)
            # fboard is board with some continuous area of empty space replaced by #
            touches_X = contact(fboard, 'X') is not None
            touches_x = contact(fboard, 'x') is not None
            if touches_X and not touches_x:
                board = fboard.replace('#', 'X')
            elif touches_x and not touches_X:
                board = fboard.replace('#', 'x')
            else:
                board = fboard.replace('#', ':')  # seki, rare
            # now that area is replaced either by X, x or :
        komi = self.komi if self.n % 2 == 1 else -self.komi
        if owner_map is not None:
            for c in range(W*W):
                n = 1 if board[c] == 'X' else -1 if board[c] == 'x' else 0
                owner_map[c] += n * (1 if self.n % 2 == 0 else -1)
        return board.count('X') - board.count('x') + komi


def empty_position():
    """ Return an initial board position """
    return Position(board=empty, cap=(0, 0), n=0, ko=None, last=None, last2=None, komi=7.5)


def fix_atari(pos, c, singlept_ok=False, twolib_test=True, twolib_edgeonly=False):

    def read_ladder_attack(pos, c, l1, l2):
        for l in [l1, l2]:
            pos_l = pos.move(l)
            if pos_l is None:
                continue
          
            is_atari, atari_escape = fix_atari(pos_l, c, twolib_test=False)
            if is_atari and not atari_escape:
                return l
        return None

    fboard = floodfill(pos.board, c)
    group_size = fboard.count('#')
    if singlept_ok and group_size == 1:
        return (False, [])
    # Find a liberty
    l = contact(fboard, '.')
    # Ok, any other liberty?
    fboard = board_put(fboard, l, 'L')
    l2 = contact(fboard, '.')
    if l2 is not None:
        # At least two liberty group...
        if twolib_test and group_size > 1 \
           and (not twolib_edgeonly or line_height(l) == 0 and line_height(l2) == 0) \
           and contact(board_put(fboard, l2, 'L'), '.') is None:
            # Exactly two liberty group with more than one stone.  Check
            # that it cannot be caught in a working ladder; if it can,
            # that's as good as in atari, a capture threat.
            # (Almost - N/A for countercaptures.)
            ladder_attack = read_ladder_attack(pos, c, l, l2)
            if ladder_attack:
                return (False, [ladder_attack])
        return (False, [])

    # In atari! If it's the opponent's group, that's enough...
    if pos.board[c] == 'x':
        return (True, [l])

    solutions = []

    # Before thinking about defense, what about counter-capturing
    # a neighboring group?
    ccboard = fboard
    while True:
        othergroup = contact(ccboard, 'x')
        if othergroup is None:
            break
        a, ccls = fix_atari(pos, othergroup, twolib_test=False)
        if a and ccls:
            solutions += ccls
        # XXX: floodfill is better for big groups
        ccboard = board_put(ccboard, othergroup, '%')

    # We are escaping.  Will playing our last liberty gain
    # at least two liberties?  Re-floodfill to account for connecting
    escpos = pos.move(l)
    if escpos is None:
        return (True, solutions)  # oops, suicidal move
    fboard = floodfill(escpos.board, l)
    l_new = contact(fboard, '.')
    fboard = board_put(fboard, l_new, 'L')
    l_new_2 = contact(fboard, '.')
    if l_new_2 is not None:
        # Good, there is still some liberty remaining - but if it's
        # just the two, check that we are not caught in a ladder...
        # (Except that we don't care if we already have some alternative
        # escape routes!)
        if solutions or not (contact(board_put(fboard, l_new_2, 'L'), '.') is None
                             and read_ladder_attack(escpos, l, l_new, l_new_2) is not None):
            solutions.append(l)

    return (True, solutions)


def cfg_distances(board, c):
 
    cfg_map = W*W*[-1]
    cfg_map[c] = 0

    # flood-fill like mechanics
    fringe = [c]
    while fringe:
        c = fringe.pop()
        for d in neighbors(c):
            if board[d].isspace() or 0 <= cfg_map[d] <= cfg_map[c]:
                continue
            cfg_before = cfg_map[d]
            if board[d] != '.' and board[d] == board[c]:
                cfg_map[d] = cfg_map[c]
            else:
                cfg_map[d] = cfg_map[c] + 1
            if cfg_before < 0 or cfg_before > cfg_map[d]:
                fringe.append(d)
    return cfg_map


def line_height(c):

    row, col = divmod(c - (W+1), W)
    return min(row, col, N-1-row, N-1-col)


def empty_area(board, c, dist=3):
  
    for d in neighbors(c):
        if board[d] in 'Xx':
            return False
        elif board[d] == '.' and dist > 1 and not empty_area(board, d, dist-1):
            return False
    return True


# 3x3 pattern routines (those patterns stored in pat3src above)

def pat3_expand(pat):
    def pat_rot90(p):
        return [p[2][0] + p[1][0] + p[0][0], p[2][1] + p[1][1] + p[0][1], p[2][2] + p[1][2] + p[0][2]]
    def pat_vertflip(p):
        return [p[2], p[1], p[0]]
    def pat_horizflip(p):
        return [l[::-1] for l in p]
    def pat_swapcolors(p):
        return [l.replace('X', 'Z').replace('x', 'z').replace('O', 'X').replace('o', 'x').replace('Z', 'O').replace('z', 'o') for l in p]
    def pat_wildexp(p, c, to):
        i = p.find(c)
        if i == -1:
            return [p]
        return reduce(lambda a, b: a + b, [pat_wildexp(p[:i] + t + p[i+1:], c, to) for t in to])
    def pat_wildcards(pat):
        return [p for p in pat_wildexp(pat, '?', list('.XO '))
                  for p in pat_wildexp(p, 'x', list('.O '))
                  for p in pat_wildexp(p, 'o', list('.X '))]
    return [p for p in [pat, pat_rot90(pat)]
              for p in [p, pat_vertflip(p)]
              for p in [p, pat_horizflip(p)]
              for p in [p, pat_swapcolors(p)]
              for p in pat_wildcards(''.join(p))]

pat3set = set([p.replace('O', 'x') for p in pat3src for p in pat3_expand(p)])

def neighborhood_33(board, c):
   
    return (board[c-W-1 : c-W+2] + board[c-1 : c+2] + board[c+W-1 : c+W+2]).replace('\n', ' ')




spat_patterndict = dict() 
def load_spat_patterndict(f):

    for line in f:
     
        if line.startswith('#'):
            continue
        neighborhood = line.split()[2].replace('#', ' ').replace('O', 'x')
        spat_patterndict[hash(neighborhood)] = int(line.split()[0])

large_patterns = dict()  

def load_large_patterns(f):
  
    for line in f:
        p = float(line.split()[0])
        m = re.search('s:(\d+)', line)
        if m is not None:
            s = int(m.groups()[0])
            large_patterns[s] = p


def neighborhood_gridcular(board, c):
   
    rotations = [((0,1),(1,1)), ((0,1),(-1,1)), ((0,1),(1,-1)), ((0,1),(-1,-1)),
                 ((1,0),(1,1)), ((1,0),(-1,1)), ((1,0),(1,-1)), ((1,0),(-1,-1))]
    neighborhood = ['' for i in range(len(rotations))]
    wboard = board.replace('\n', ' ')
    for dseq in pat_gridcular_seq:
        for ri in range(len(rotations)):
            r = rotations[ri]
            for o in dseq:
                y, x = divmod(c - (W+1), W)
                y += o[r[0][0]]*r[1][0]
                x += o[r[0][1]]*r[1][1]
                if y >= 0 and y < N and x >= 0 and x < N:
                    neighborhood[ri] += wboard[(y+1)*W + x+1]
                else:
                    neighborhood[ri] += ' '
            yield neighborhood[ri]


def large_pattern_probability(board, c):
    
    probability = None
    matched_len = 0
    non_matched_len = 0
    for n in neighborhood_gridcular(board, c):
        sp_i = spat_patterndict.get(hash(n))
        prob = large_patterns.get(sp_i) if sp_i is not None else None
        if prob is not None:
            probability = prob
            matched_len = len(n)
        elif matched_len < non_matched_len < len(n):
            break
        else:
            non_matched_len = len(n)
    return probability

def gen_playout_moves(pos, heuristic_set, probs={'capture': 1, 'pat3': 1}, expensive_ok=False):
  
    if random.random() <= probs['capture']:
        already_suggested = set()
        for c in heuristic_set:
            if pos.board[c] in 'Xx':
                in_atari, ds = fix_atari(pos, c, twolib_edgeonly=not expensive_ok)
                random.shuffle(ds)
                for d in ds:
                    if d not in already_suggested:
                        yield (d, 'capture '+str(c))
                        already_suggested.add(d)

    # Try to apply a 3x3 pattern on the local neighborhood
    if random.random() <= probs['pat3']:
        already_suggested = set()
        for c in heuristic_set:
            if pos.board[c] == '.' and c not in already_suggested and neighborhood_33(pos.board, c) in pat3set:
                yield (c, 'pat3')
                already_suggested.add(c)

    # Try *all* available moves, but starting from a random point
    # (in other words, suggest a random move)
    x, y = random.randint(1, N), random.randint(1, N)
    for c in pos.moves(y*W + x):
        yield (c, 'random')


def mcplayout(pos, amaf_map, disp=False):
    """ Start a Monte Carlo playout from a given position,
    return score for to-play player at the starting position;
    amaf_map is board-sized scratchpad recording who played at a given
    position first """
    if disp:  print('** SIMULATION **', file=sys.stderr)
    start_n = pos.n
    passes = 0
    while passes < 2 and pos.n < 200 :
        if disp:  print_pos(pos)

        pos2 = None
        # We simply try the moves our heuristics generate, in a particular
        # order, but not with 100% probability; this is on the border between
        # "rule-based playouts" and "probability distribution playouts".
        for c, kind in gen_playout_moves(pos, pos.last_moves_neighbors(), PROB_HEURISTIC):
            if disp and kind != 'random':
                print('move suggestion', str_coord(c), kind, file=sys.stderr)
            pos2 = pos.move(c)
            if pos2 is None:
                continue
            # check if the suggested move did not turn out to be a self-atari
            if random.random() <= (PROB_RSAREJECT if kind == 'random' else PROB_SSAREJECT):
                in_atari, ds = fix_atari(pos2, c, singlept_ok=True, twolib_edgeonly=True)
                if ds:
                    if disp:  print('rejecting self-atari move', str_coord(c), file=sys.stderr)
                    pos2 = None
                    continue
            if amaf_map[c] == 0:  # Mark the coordinate with 1 for black
                amaf_map[c] = 1 if pos.n % 2 == 0 else -1
            break
        if pos2 is None:  # no valid moves, pass
            pos = pos.pass_move()
            passes += 1
            continue
        passes = 0
        pos = pos2

    owner_map = W*W*[0]
    score = pos.score(owner_map)
    if disp:  print('** SCORE B%+.1f **' % (score if pos.n % 2 == 0 else -score), file=sys.stderr)
    if start_n % 2 != pos.n % 2:
        score = -score
    return score, amaf_map, owner_map

class TreeNode():
 
    def __init__(self, pos):
        self.pos = pos
        self.v = 0
        self.w = 0
        self.pv = PRIOR_EVEN
        self.pw = PRIOR_EVEN/2
        self.av = 0
        self.aw = 0
        self.children = None

    def expand(self):
        """ add and initialize children to a leaf node """
        cfg_map = cfg_distances(self.pos.board, self.pos.last) if self.pos.last is not None else None
        self.children = []
        childset = dict()
        # Use playout generator to generate children and initialize them
        # with some priors to bias search towards more sensible moves.
        # Note that there can be many ways to incorporate the priors in
        # next node selection (progressive bias, progressive widening, ...).
        for c, kind in gen_playout_moves(self.pos, range(N, (N+1)*W), expensive_ok=True):
            pos2 = self.pos.move(c)
            if pos2 is None:
                continue
            # gen_playout_moves() will generate duplicate suggestions
            # if a move is yielded by multiple heuristics
            try:
                node = childset[pos2.last]
            except KeyError:
                node = TreeNode(pos2)
                self.children.append(node)
                childset[pos2.last] = node

            if kind.startswith('capture'):
                # Check how big group we are capturing; coord of the group is
                # second word in the ``kind`` string
                if floodfill(self.pos.board, int(kind.split()[1])).count('#') > 1:
                    node.pv += PRIOR_CAPTURE_MANY
                    node.pw += PRIOR_CAPTURE_MANY
                else:
                    node.pv += PRIOR_CAPTURE_ONE
                    node.pw += PRIOR_CAPTURE_ONE
            elif kind == 'pat3':
                node.pv += PRIOR_PAT3
                node.pw += PRIOR_PAT3

        # Second pass setting priors, considering each move just once now
        for node in self.children:
            c = node.pos.last

            if cfg_map is not None and cfg_map[c]-1 < len(PRIOR_CFG):
                node.pv += PRIOR_CFG[cfg_map[c]-1]
                node.pw += PRIOR_CFG[cfg_map[c]-1]

            height = line_height(c)  # 0-indexed
            if height <= 2 and empty_area(self.pos.board, c):
                # No stones around; negative prior for 1st + 2nd line, positive
                # for 3rd line; sanitizes opening and invasions
                if height <= 1:
                    node.pv += PRIOR_EMPTYAREA
                    node.pw += 0
                if height == 2:
                    node.pv += PRIOR_EMPTYAREA
                    node.pw += PRIOR_EMPTYAREA

            in_atari, ds = fix_atari(node.pos, c, singlept_ok=True)
            if ds:
                node.pv += PRIOR_SELFATARI
                node.pw += 0  # negative prior

            patternprob = large_pattern_probability(self.pos.board, c)
            if patternprob is not None and patternprob > 0.001:
                pattern_prior = math.sqrt(patternprob)  # tone up
                node.pv += pattern_prior * PRIOR_LARGEPATTERN
                node.pw += pattern_prior * PRIOR_LARGEPATTERN

        if not self.children:
            # No possible moves, add a pass move
            self.children.append(TreeNode(self.pos.pass_move()))

    def rave_urgency(self):
        v = self.v + self.pv
        expectation = float(self.w+self.pw) / v
        if self.av == 0:
            return expectation
        rave_expectation = float(self.aw) / self.av
        beta = self.av / (self.av + v + float(v) * self.av / RAVE_EQUIV)
        return beta * rave_expectation + (1-beta) * expectation

    def winrate(self):
        return float(self.w) / self.v if self.v > 0 else float('nan')

    def best_move(self):
        """ best move is the most simulated one """
        return max(self.children, key=lambda node: node.v) if self.children is not None else None


def tree_descend(tree, amaf_map, disp=False):
    """ Descend through the tree to a leaf """
    tree.v += 1
    nodes = [tree]
    passes = 0
    while nodes[-1].children is not None and passes < 2:
        if disp:  print_pos(nodes[-1].pos)

        # Pick the most urgent child
        children = list(nodes[-1].children)
        if disp:
            for c in children:
                dump_subtree(c, recurse=False)
        random.shuffle(children)  # randomize the max in case of equal urgency
        node = max(children, key=lambda node: node.rave_urgency())
        nodes.append(node)

        if disp:  print('chosen %s' % (str_coord(node.pos.last),), file=sys.stderr)
        if node.pos.last is None:
            passes += 1
        else:
            passes = 0
            if amaf_map[node.pos.last] == 0:  # Mark the coordinate with 1 for black
                amaf_map[node.pos.last] = 1 if nodes[-2].pos.n % 2 == 0 else -1

        # updating visits on the way *down* represents "virtual loss", relevant for parallelization
        node.v += 1
        if node.children is None and node.v >= EXPAND_VISITS:
            node.expand()

    return nodes


def tree_update(nodes, amaf_map, score, disp=False):
    """ Store simulation result in the tree (@nodes is the tree path) """
    for node in reversed(nodes):
        if disp:  print('updating', str_coord(node.pos.last), score < 0, file=sys.stderr)
        node.w += score < 0  # score is for to-play, node statistics for just-played
        # Update the node children AMAF stats with moves we made
        # with their color
        amaf_map_value = 1 if node.pos.n % 2 == 0 else -1
        if node.children is not None:
            for child in node.children:
                if child.pos.last is None:
                    continue
                if amaf_map[child.pos.last] == amaf_map_value:
                    if disp:  print('  AMAF updating', str_coord(child.pos.last), score > 0, file=sys.stderr)
                    child.aw += score > 0  # reversed perspective
                    child.av += 1
        score = -score


worker_pool = None

def tree_search(tree, n, owner_map, disp=False):
   
    if tree.children is None:
        tree.expand()

    n_workers = multiprocessing.cpu_count() if not disp else 1  # set to 1 when debugging
    global worker_pool
    if worker_pool is None:
        worker_pool = Pool(processes=n_workers)
    outgoing = []  # positions waiting for a playout
    incoming = []  # positions that finished evaluation
    ongoing = []  # currently ongoing playout jobs
    i = 0
    while i < n:
        if not outgoing and not (disp and ongoing):
            # Descend the tree so that we have something ready when a worker
            # stops being busy
            amaf_map = W*W*[0]
            nodes = tree_descend(tree, amaf_map, disp=disp)
            outgoing.append((nodes, amaf_map))

        if len(ongoing) >= n_workers:
            # Too many playouts running? Wait a bit...
            ongoing[0][0].wait(0.01 / n_workers)
        else:
            i += 1
            if i > 0 and i % REPORT_PERIOD == 0:
                print_tree_summary(tree, i, f=sys.stderr)

            # Issue an mcplayout job to the worker pool
            nodes, amaf_map = outgoing.pop()
            ongoing.append((worker_pool.apply_async(mcplayout, (nodes[-1].pos, amaf_map, disp)), nodes))

        # Anything to store in the tree?  (We do this step out-of-order
        # picking up data from the previous round so that we don't stall
        # ready workers while we update the tree.)
        while incoming:
            score, amaf_map, owner_map_one, nodes = incoming.pop()
            tree_update(nodes, amaf_map, score, disp=disp)
            for c in range(W*W):
                owner_map[c] += owner_map_one[c]

        # Any playouts are finished yet?
        for job, nodes in ongoing:
            if not job.ready():
                continue
            # Yes! Queue them up for storing in the tree.
            score, amaf_map, owner_map_one = job.get()
            incoming.append((score, amaf_map, owner_map_one, nodes))
            ongoing.remove((job, nodes))

        # Early stop test
        best_wr = tree.best_move().winrate()
        if i > n*0.05 and best_wr > FASTPLAY5_THRES or i > n*0.2 and best_wr > FASTPLAY20_THRES:
            break

    for c in range(W*W):
        owner_map[c] = float(owner_map[c]) / i
    dump_subtree(tree)
    print_tree_summary(tree, i, f=sys.stderr)
    return tree.best_move()


def print_pos(pos, f=sys.stderr, owner_map=None):

    if pos.n % 2 == 0:  # to-play is black
        board = pos.board.replace('x', 'O')
        Xcap, Ocap = pos.cap
    else:  # to-play is white
        board = pos.board.replace('X', 'O').replace('x', 'X')
        Ocap, Xcap = pos.cap
    print('Move: %-3d   Black: %d caps   White: %d caps  Komi: %.1f' % (pos.n, Xcap, Ocap, pos.komi), file=f)
    pretty_board = ' '.join(board.rstrip()) + ' '
    if pos.last is not None:
        pretty_board = pretty_board[:pos.last*2-1] + '(' + board[pos.last] + ')' + pretty_board[pos.last*2+2:]
    rowcounter = count()
    pretty_board = [' %-02d%s' % (N-i, row[2:]) for row, i in zip(pretty_board.split("\n")[1:], rowcounter)]
    if owner_map is not None:
        pretty_ownermap = ''
       
        for c in range(W*W):
            if board[c].isspace():
                pretty_ownermap += board[c]

          
      
        pretty_board = ['%s   %s' % (brow, orow[2:]) for brow, orow in zip(pretty_board, pretty_ownermap.split("\n")[1:])]
    print("\n".join(pretty_board), file=f)
    print('    ' + ' '.join(colstr[:N]), file=f)
    print('', file=f)


def dump_subtree(node, thres=N_SIMS/50, indent=0, f=sys.stderr, recurse=True):

    if not recurse:
        return
    for child in sorted(node.children, key=lambda n: n.v, reverse=True):
        if child.v >= thres:
            dump_subtree(child, thres=thres, indent=indent+3, f=f)


def print_tree_summary(tree, sims, f=sys.stderr):
    best_nodes = sorted(tree.children, key=lambda n: n.v, reverse=True)[:5]
    best_seq = []
    node = tree
    while node is not None:
        best_seq.append(node.pos.last)
        node = node.best_move()
    #print('[%4d] winrate %.3f | seq %s | can %s' %
     #     (sims, best_nodes[0].winrate(), ' '.join([str_coord(c) for c in best_seq[1:6]]),
         #  ' '.join(['%s(%.3f)' % (str_coord(n.pos.last), n.winrate()) for n in best_nodes])), file=f)

def parse_coord(s):
    if s == 'pass':
        return None
    return W+1 + (N - int(s[1:])) * W + colstr.index(s[0].upper())


def str_coord(c):
    end=time.time()
    if end-start>9:
        return 'pass'
    if c is None:
        return 'pass'
    row, col = divmod(c - (W+1), W)
    return '%c%d' % (colstr[col], N - row)

def go(arg):
    global start
    start=time.time()
    tree = TreeNode(Position(board=arg, cap=(0, 0), n=0, ko=None, last=None, last2=None, komi=7.5))
    tree.expand()
    owner_map = W*W*[0]
    tree = tree_search(tree, N_SIMS, owner_map)
    ret = str_coord(tree.pos.last)
    # if ret.len() < 2:
    #     print('pass')
    print('%s ' % ret)
    return ret
    
if __name__ == '__main__':
    go(sys.argv[1])