// golocate: the Go half of BugForge's Phase 2 for Go source.
//
// It does exactly two things, both of them read-only:
//
//	-mode=locate       parse one file and emit every mutable token's position
//	-mode=check        parse one file and say whether it is syntactically valid
//	-mode=fingerprint  count the constructs cloud/anti_cheat.py compares
//
// It never rewrites source. Splicing stays in Python (bugforge/mutate.py),
// for the same reason the Python adapter never calls ast.unparse: a
// formatter round-trip would rewrite lines nobody mutated. All this program
// emits is positions.
//
// Positions are (line, byte offset into that line), zero-based on the column,
// which is what MutationSite means by col_start/col_end. go/token's Position
// gives a 1-based *byte* column, so the conversion is a subtraction and no
// rune decoding is involved anywhere -- the same discipline mutate.py keeps
// on the Python side.
//
// I/O is JSON on stdin and stdout so the caller never has to quote a Go
// source file through a shell.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"io"
	"os"
	"strconv"
	"strings"
)

type request struct {
	Path   string `json:"path"`
	Source string `json:"source"`
}

// site mirrors bugforge.models.MutationSite field for field, in the JSON
// spelling the Python side expects.
type site struct {
	Path                  string `json:"path"`
	Lineno                int    `json:"lineno"`
	ColStart              int    `json:"col_start"`
	ColEnd                int    `json:"col_end"`
	OperatorID            string `json:"operator_id"`
	OriginalToken         string `json:"original_token"`
	MutatedToken          string `json:"mutated_token"`
	EnclosingFunctionName string `json:"enclosing_function_name"`
	EnclosingClassName    string `json:"enclosing_class_name"`
}

type response struct {
	Sites       []site       `json:"sites"`
	Fingerprint *fingerprint `json:"fingerprint,omitempty"`
	Error       string       `json:"error,omitempty"`
}

// fingerprint is the Go half of cloud/anti_cheat.py's before/after
// comparison. It is deliberately shorter than the Python one: a learner's
// patch can only touch non-test .go files, and Go source has no `assert`
// statement and no skip markers outside a test file, so the rules that
// survive the translation are the ones about ending the run early.
type fingerprint struct {
	// os.Exit / syscall.Exit / runtime.Goexit. The dangerous one is
	// os.Exit(0): it ends the test binary with a success status before any
	// result is printed, and `go test` reports "ok" over the top of it.
	ExitCalls int `json:"exit_calls"`
	// t.Skip / t.SkipNow / testing.Short guards added to production code that
	// a test calls. Rare, but free to count while we are already walking.
	SkipCalls int `json:"skip_calls"`
	// A file that compiles out of the build entirely answers no assertions.
	BuildIgnores int `json:"build_ignores"`
}

// Binary operators worth flipping, and what to flip them to. These mirror
// bugforge/mutate.py's tables one for one, with two Go-specific additions
// noted below.
var binaryMutations = map[token.Token]struct{ from, to string }{
	// COMPARISON
	token.EQL: {"==", "!="},
	token.LSS: {"<", "<="},
	token.LEQ: {"<=", "<"},
	token.GTR: {">", ">="},
	token.GEQ: {">=", ">"},
	// NEQ has no counterpart in the Python table, which only mutates Eq.
	// It earns its place here because `if err != nil` is the single most
	// common control-flow decision in Go source; flipping it produces a
	// defect that compiles, runs, and reads as a plausible human mistake.
	token.NEQ: {"!=", "=="},
	// ARITHMETIC
	token.ADD: {"+", "-"},
	token.SUB: {"-", "+"},
	// Python mutates Mult to floor division to keep the result an int. Go's
	// `/` is already integer division on integer operands, so it is the
	// direct equivalent.
	token.MUL: {"*", "/"},
	// BOOLEAN
	token.LAND: {"&&", "||"},
	token.LOR:  {"||", "&&"},
}

func operatorID(tok token.Token) string {
	switch tok {
	case token.EQL, token.NEQ, token.LSS, token.LEQ, token.GTR, token.GEQ:
		return "COMPARISON"
	case token.ADD, token.SUB, token.MUL:
		return "ARITHMETIC"
	case token.LAND, token.LOR:
		return "BOOLEAN"
	}
	return ""
}

func isComparison(tok token.Token) bool {
	return operatorID(tok) == "COMPARISON"
}

// Go's answer to __repr__/__str__. A defect inside one of these shows up as
// a mangled string in an assertion message rather than as behaviour a
// learner can reason about, so mutate.py skips them and so do we.
var skipFunctions = map[string]bool{
	"String":   true,
	"Error":    true,
	"GoString": true,
	"Format":   true,
}

type locator struct {
	fset  *token.FileSet
	path  string
	sites []site
	// stack is the chain of ancestors of the node being visited, innermost
	// last. It answers both "what function am I in" and "what is my parent",
	// which is what mutate.py builds a whole parent map for.
	stack []ast.Node
}

// enclosingDecl returns the innermost named function the cursor sits inside.
// A FuncLit does not shadow it: a closure defined in Parse() is still, to a
// learner reading a stack trace, part of Parse().
func (l *locator) enclosingDecl() *ast.FuncDecl {
	for i := len(l.stack) - 1; i >= 0; i-- {
		if d, ok := l.stack[i].(*ast.FuncDecl); ok {
			return d
		}
	}
	return nil
}

func (l *locator) parent() ast.Node {
	// stack ends with the node currently being visited, so its parent is
	// one further back.
	if len(l.stack) < 2 {
		return nil
	}
	return l.stack[len(l.stack)-2]
}

func (l *locator) enclosing() (fn string, recv string) {
	d := l.enclosingDecl()
	if d == nil {
		return "", ""
	}
	fn = d.Name.Name
	if d.Recv != nil && len(d.Recv.List) > 0 {
		recv = typeName(d.Recv.List[0].Type)
	}
	return fn, recv
}

// typeName renders a receiver type as a bare identifier: `*Parser` and
// `Parser` both come back as "Parser". Title copy only -- nothing downstream
// resolves it.
func typeName(expr ast.Expr) string {
	switch t := expr.(type) {
	case *ast.StarExpr:
		return typeName(t.X)
	case *ast.Ident:
		return t.Name
	case *ast.IndexExpr: // generic receiver, e.g. Foo[T]
		return typeName(t.X)
	case *ast.IndexListExpr:
		return typeName(t.X)
	}
	return ""
}

func (l *locator) inSkippedFunction() bool {
	for _, n := range l.stack {
		if d, ok := n.(*ast.FuncDecl); ok && skipFunctions[d.Name.Name] {
			return true
		}
	}
	return false
}

// add records one site, converting go/token's 1-based byte column to the
// zero-based byte offset MutationSite uses. width is the byte length of the
// original token; a mutation that deletes a token passes the replacement "".
func (l *locator) add(pos token.Pos, width int, opID, original, mutated string) {
	if original == mutated || l.inSkippedFunction() {
		return
	}
	p := l.fset.Position(pos)
	if !p.IsValid() || p.Column < 1 {
		return
	}
	fn, recv := l.enclosing()
	l.sites = append(l.sites, site{
		Path:                  l.path,
		Lineno:                p.Line,
		ColStart:              p.Column - 1,
		ColEnd:                p.Column - 1 + width,
		OperatorID:            opID,
		OriginalToken:         original,
		MutatedToken:          mutated,
		EnclosingFunctionName: fn,
		EnclosingClassName:    recv,
	})
}

// spanOf returns the (line, start, end) byte span of a node that sits
// entirely on one line, or ok=false if it spans lines.
func (l *locator) spanOf(node ast.Node) (line, start, end int, ok bool) {
	s := l.fset.Position(node.Pos())
	e := l.fset.Position(node.End())
	if !s.IsValid() || !e.IsValid() || s.Line != e.Line {
		return 0, 0, 0, false
	}
	return s.Line, s.Column - 1, e.Column - 1, true
}

// visit dispatches one node to whichever operators can apply to it. It is
// deliberately a flat switch in the same order as mutate.py's, so the two
// languages' operator sets can be diffed by eye.
func (l *locator) visit(n ast.Node) {
	switch node := n.(type) {
	case *ast.BinaryExpr:
		l.binary(node)
	case *ast.UnaryExpr:
		l.negation(node)
	case *ast.BasicLit:
		l.boundary(node)
	case *ast.ReturnStmt:
		l.returns(node)
	}
}

// binary covers COMPARISON, ARITHMETIC and BOOLEAN in one place, because in
// Go all three are *ast.BinaryExpr and OpPos gives the operator's exact
// position -- no searching the gap between operands, which is what mutate.py
// has to do because Python's ast does not record operator positions.
func (l *locator) binary(node *ast.BinaryExpr) {
	m, ok := binaryMutations[node.Op]
	if !ok {
		return
	}
	// String concatenation is spelled with "+" like addition. Turning it into
	// "-" is a compile error, not a defect, so it would be dropped downstream
	// as a wasted candidate -- but only after a full build. Cheap to catch
	// here: if either side is a string literal, leave it alone.
	if node.Op == token.ADD && (isStringLit(node.X) || isStringLit(node.Y)) {
		return
	}
	l.add(node.OpPos, len(m.from), operatorID(node.Op), m.from, m.to)
}

func isStringLit(e ast.Expr) bool {
	lit, ok := e.(*ast.BasicLit)
	return ok && lit.Kind == token.STRING
}

// negation deletes the "!" from a boolean negation, turning `if !ok {` into
// `if ok {`. Unlike the Python operator this needs no parenthesis special
// case: we delete exactly the one "!" byte, so `!(a || b)` becomes
// `(a || b)`, which is still valid Go.
func (l *locator) negation(node *ast.UnaryExpr) {
	if node.Op != token.NOT {
		return
	}
	l.add(node.OpPos, 1, "NEGATION", "!", "")
}

// boundary nudges an integer literal by one, but only where being off by one
// changes behaviour rather than failing to compile: an index, a slice bound,
// or one side of a comparison. A literal in a const block or an array length
// is left alone.
func (l *locator) boundary(node *ast.BasicLit) {
	if node.Kind != token.INT {
		return
	}
	value, err := strconv.Atoi(node.Value)
	if err != nil { // hex, binary, underscores, or larger than int -- skip
		return
	}
	interesting := false
	switch p := l.parent().(type) {
	case *ast.IndexExpr:
		interesting = p.Index == node
	case *ast.SliceExpr:
		interesting = p.Low == node || p.High == node || p.Max == node
	case *ast.BinaryExpr:
		interesting = isComparison(p.Op) && (p.X == node || p.Y == node)
	}
	if !interesting {
		return
	}
	_, start, end, ok := l.spanOf(node)
	if !ok {
		return
	}
	for _, delta := range []int{1, -1} {
		l.addSpan(node.Pos(), start, end, "BOUNDARY", node.Value, strconv.Itoa(value+delta))
	}
}

// returns flips a bare `return true` / `return false`. Go's returns are
// typed and often multi-valued, so unlike Python -- where any returned
// expression can become None -- this is the only rewrite that is guaranteed
// to compile without consulting the type checker.
func (l *locator) returns(node *ast.ReturnStmt) {
	if len(node.Results) != 1 {
		return
	}
	ident, ok := node.Results[0].(*ast.Ident)
	if !ok {
		return
	}
	var mutated string
	switch ident.Name {
	case "true":
		mutated = "false"
	case "false":
		mutated = "true"
	default:
		return
	}
	_, start, end, spanOK := l.spanOf(ident)
	if !spanOK {
		return
	}
	l.addSpan(ident.Pos(), start, end, "RETURN", ident.Name, mutated)
}

// addSpan is add() for a token whose span was measured rather than derived
// from a known width.
func (l *locator) addSpan(pos token.Pos, start, end int, opID, original, mutated string) {
	if original == mutated || l.inSkippedFunction() {
		return
	}
	p := l.fset.Position(pos)
	if !p.IsValid() {
		return
	}
	fn, recv := l.enclosing()
	l.sites = append(l.sites, site{
		Path:                  l.path,
		Lineno:                p.Line,
		ColStart:              start,
		ColEnd:                end,
		OperatorID:            opID,
		OriginalToken:         original,
		MutatedToken:          mutated,
		EnclosingFunctionName: fn,
		EnclosingClassName:    recv,
	})
}

func locate(req request) response {
	fset := token.NewFileSet()
	file, err := parser.ParseFile(fset, req.Path, req.Source, parser.SkipObjectResolution)
	if err != nil {
		return response{Error: err.Error()}
	}
	l := &locator{fset: fset, path: req.Path, sites: []site{}}
	ast.Inspect(file, func(n ast.Node) bool {
		if n == nil {
			// Inspect signals "done with this subtree" with a nil node, and
			// only ever does so for a node we pushed.
			l.stack = l.stack[:len(l.stack)-1]
			return false
		}
		l.stack = append(l.stack, n)
		l.visit(n)
		return true
	})
	// Deterministic order. The Python side thins this list to
	// MAX_CANDIDATES_PER_FILE with a fixed stride, so the order here decides
	// which candidates survive -- it must not depend on map iteration.
	sortSites(l.sites)
	return response{Sites: l.sites}
}

func sortSites(s []site) {
	// Insertion sort: files top out around a few hundred sites and this
	// avoids pulling in a comparison closure just to read the same four keys.
	for i := 1; i < len(s); i++ {
		for j := i; j > 0 && less(s[j], s[j-1]); j-- {
			s[j], s[j-1] = s[j-1], s[j]
		}
	}
}

func less(a, b site) bool {
	if a.Lineno != b.Lineno {
		return a.Lineno < b.Lineno
	}
	if a.ColStart != b.ColStart {
		return a.ColStart < b.ColStart
	}
	if a.OperatorID != b.OperatorID {
		return a.OperatorID < b.OperatorID
	}
	return a.MutatedToken < b.MutatedToken
}

// Calls that end the process or the goroutine running a test.
var exitCalls = map[string]bool{
	"os.Exit":        true,
	"syscall.Exit":   true,
	"runtime.Goexit": true,
	// log.Fatal* calls os.Exit(1) -- a failing status, so useless as a cheat,
	// but counted because "the suite stopped early" should never be a way to
	// change a verdict regardless of which direction it points.
	"log.Fatal":   true,
	"log.Fatalf":  true,
	"log.Fatalln": true,
}

var skipCalls = map[string]bool{"Skip": true, "SkipNow": true, "Skipf": true}

// callName renders `os.Exit` / `t.Skip` from a call's function expression.
func callName(expr ast.Expr) string {
	switch fn := expr.(type) {
	case *ast.Ident:
		return fn.Name
	case *ast.SelectorExpr:
		if x, ok := fn.X.(*ast.Ident); ok {
			return x.Name + "." + fn.Sel.Name
		}
		return fn.Sel.Name
	}
	return ""
}

func fingerprintFile(req request) response {
	fset := token.NewFileSet()
	file, err := parser.ParseFile(fset, req.Path, req.Source, parser.ParseComments|parser.SkipObjectResolution)
	if err != nil {
		return response{Error: err.Error()}
	}
	fp := &fingerprint{}
	ast.Inspect(file, func(n ast.Node) bool {
		call, ok := n.(*ast.CallExpr)
		if !ok {
			return true
		}
		name := callName(call.Fun)
		if exitCalls[name] {
			fp.ExitCalls++
		}
		// Match on the method name alone: the receiver is *testing.T under
		// any local name a learner cares to give it.
		if idx := strings.LastIndex(name, "."); idx >= 0 && skipCalls[name[idx+1:]] {
			fp.SkipCalls++
		}
		return true
	})
	for _, group := range file.Comments {
		for _, c := range group.List {
			text := strings.TrimSpace(c.Text)
			if strings.HasPrefix(text, "//go:build") || strings.HasPrefix(text, "// +build") {
				fp.BuildIgnores++
			}
		}
	}
	return response{Sites: []site{}, Fingerprint: fp}
}

func check(req request) response {
	fset := token.NewFileSet()
	if _, err := parser.ParseFile(fset, req.Path, req.Source, parser.SkipObjectResolution); err != nil {
		return response{Error: err.Error()}
	}
	return response{Sites: []site{}}
}

func main() {
	mode := flag.String("mode", "locate", "locate | check | fingerprint")
	flag.Parse()

	raw, err := io.ReadAll(os.Stdin)
	if err != nil {
		fail("reading stdin: %v", err)
	}
	var req request
	if err := json.Unmarshal(raw, &req); err != nil {
		fail("decoding request: %v", err)
	}
	if strings.TrimSpace(req.Path) == "" {
		fail("request needs a non-empty path")
	}

	var resp response
	switch *mode {
	case "locate":
		resp = locate(req)
	case "check":
		resp = check(req)
	case "fingerprint":
		resp = fingerprintFile(req)
	default:
		fail("unknown -mode %q", *mode)
	}

	// A parse error is a normal answer, not a crash: mutate.apply asks this
	// program whether a spliced file still compiles and expects "no" to come
	// back as data. Process exit status is reserved for the program itself
	// failing.
	if err := json.NewEncoder(os.Stdout).Encode(resp); err != nil {
		fail("encoding response: %v", err)
	}
}

func fail(format string, args ...any) {
	fmt.Fprintf(os.Stderr, format+"\n", args...)
	os.Exit(2)
}
