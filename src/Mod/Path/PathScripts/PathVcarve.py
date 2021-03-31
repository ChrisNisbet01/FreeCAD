# -*- coding: utf-8 -*-
# ***************************************************************************
# *                                                                         *
# *   Copyright (c) 2020 sliptonic <shopinthewoods@gmail.com>               *
# *                                                                         *
# *   This program is free software; you can redistribute it and/or modify  *
# *   it under the terms of the GNU Lesser General Public License (LGPL)    *
# *   as published by the Free Software Foundation; either version 2 of     *
# *   the License, or (at your option) any later version.                   *
# *   for detail see the LICENCE text file.                                 *
# *                                                                         *
# *   This program is distributed in the hope that it will be useful,       *
# *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
# *   GNU Library General Public License for more details.                  *
# *                                                                         *
# *   You should have received a copy of the GNU Library General Public     *
# *   License along with this program; if not, write to the Free Software   *
# *   Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  *
# *   USA                                                                   *
# *                                                                         *
# ***************************************************************************

import FreeCAD
import Part
import Path
import PathScripts.PathEngraveBase as PathEngraveBase
import PathScripts.PathLog as PathLog
import PathScripts.PathOp as PathOp
import PathScripts.PathUtils as PathUtils
import PathScripts.PathGeom as PathGeom
import PathScripts.PathPreferences as PathPreferences
import PathScripts.PathWidthToDepth as PathWidthToDepth

import traceback

import math

from PySide import QtCore

__doc__ = "Class and implementation of Path Vcarve operation"

PRIMARY   = 0
SECONDARY = 1
EXTERIOR1 = 2
EXTERIOR2 = 3
COLINEAR  = 4
TWIN      = 5

PathLog.setLevel(PathLog.Level.INFO, PathLog.thisModule())
# PathLog.trackModule(PathLog.thisModule())


# Qt translation handling
def translate(context, text, disambig=None):
    return QtCore.QCoreApplication.translate(context, text, disambig)


VD = []
Vertex = {}

_sorting = 'global'


def _collectVoronoiWires(vd):
    edges = [e for e in vd.Edges if e.Color == PRIMARY]
    vertex = {}
    for e in edges:
        for v in e.Vertices:
            i = v.Index
            j = vertex.get(i, [])
            j.append(e)
            vertex[i] = j
    Vertex.clear()
    for v in vertex:
        Vertex[v] = vertex[v]

    # knots are the start and end points of a wire
    knots = [i for i in vertex if len(vertex[i]) == 1]
    knots.extend([i for i in vertex if len(vertex[i]) > 2])
    if len(knots) == 0:
        for i in vertex:
            if len(vertex[i]) > 0:
                knots.append(i)
                break

    def consume(v, edge):
        vertex[v] = [e for e in vertex[v] if e.Index != edge.Index]
        return len(vertex[v]) == 0

    def traverse(vStart, edge, edges):
        if vStart == edge.Vertices[0].Index:
            vEnd = edge.Vertices[1].Index
            edges.append(edge)
        else:
            vEnd = edge.Vertices[0].Index
            edges.append(edge.Twin)

        consume(vStart, edge)
        if consume(vEnd, edge):
            return None
        return vEnd

    wires = []
    while knots:
        we = []
        vFirst = knots[0]
        vStart = vFirst
        vLast  = vFirst
        if len(vertex[vStart]):
            while vStart is not None:
                vLast  = vStart
                edges  = vertex[vStart]
                if len(edges) > 0:
                    edge   = edges[0]
                    vStart = traverse(vStart, edge, we)
                else:
                    vStart = None
            wires.append(we)
        if len(vertex[vFirst]) == 0:
            knots = [v for v in knots if v != vFirst]
        if len(vertex[vLast]) == 0:
            knots = [v for v in knots if v != vLast]
    return wires


def _sortVoronoiWires(wires, start=FreeCAD.Vector(0, 0, 0)):
    def closestTo(start, point):
        p = None
        l = None
        for i in point:
            if l is None or l > start.distanceToPoint(point[i]):
                l = start.distanceToPoint(point[i])
                p = i
        return (p, l)

    begin = {}
    end   = {}

    for i, w in enumerate(wires):
        begin[i] = w[0].Vertices[0].toPoint()
        end[i]   = w[-1].Vertices[1].toPoint()

    result = []
    while begin:
        (bIdx, bLen) = closestTo(start, begin)
        (eIdx, eLen) = closestTo(start, end)
        if bLen < eLen:
            result.append(wires[bIdx])
            start =   end[bIdx]
            del     begin[bIdx]
            del       end[bIdx]
        else:
            result.append([e.Twin for e in reversed(wires[eIdx])])
            start = begin[eIdx]
            del     begin[eIdx]
            del       end[eIdx]

    return result

def _calculate_depth(MIC, width_to_depth_calculator):
    # given a maximum inscribed circle (MIC) and tool angle,
    # return depth of cut relative to zStart.
    depth = round(width_to_depth_calculator.WidthToDepth(MIC), 4)
    PathLog.debug('depth: {}'.format(depth))

    return depth

def _getPartEdge(edge, width_to_depth_calculator):
    dist = edge.getDistances()
    zBegin = _calculate_depth(dist[0], width_to_depth_calculator)
    zEnd = _calculate_depth(dist[1], width_to_depth_calculator)

    return edge.toShape(zBegin, zEnd)

class ObjectVcarve(PathEngraveBase.ObjectOp):
    '''Proxy class for Vcarve operation.'''

    def opFeatures(self, obj):
        '''opFeatures(obj) ... return all standard features and edges based geomtries'''
        return PathOp.FeatureTool | PathOp.FeatureHeights | PathOp.FeatureDepths | PathOp.FeatureBaseFaces | PathOp.FeatureCoolant

    def setupAdditionalProperties(self, obj):
        if not hasattr(obj, 'BaseShapes'):
            obj.addProperty("App::PropertyLinkList", "BaseShapes", "Path",
                            QtCore.QT_TRANSLATE_NOOP("PathVcarve",
                                "Additional base objects to be engraved"))
        obj.setEditorMode('BaseShapes', 2)  # hide

    def initOperation(self, obj):
        '''initOperation(obj) ... create vcarve specific properties.'''
        obj.addProperty("App::PropertyFloat", "Discretize", "Path",
                        QtCore.QT_TRANSLATE_NOOP("PathVcarve",
                        "The deflection value for discretizing arcs"))
        obj.addProperty("App::PropertyFloat", "Colinear", "Path",
                        QtCore.QT_TRANSLATE_NOOP("PathVcarve",
                        "Cutoff for removing colinear segments (degrees). \
                        default=10.0."))
        obj.addProperty("App::PropertyFloat", "Tolerance", "Path",
                QtCore.QT_TRANSLATE_NOOP("PathVcarve", ""))
        obj.Colinear = 10.0
        obj.Discretize = 0.01
        obj.Tolerance = PathPreferences.defaultGeometryTolerance()
        self.setupAdditionalProperties(obj)

    def opOnDocumentRestored(self, obj):
        # upgrade ...
        self.setupAdditionalProperties(obj)

    def _getPartEdges(self, obj, vWire, width_to_depth_calculator):
        def simplify_edges(edges):
            def median(v1, v2):
                vd = v2.sub(v1)
                vd.scale(0.5, 0.5, 0.5)
                return v1.add(vd)

            points = []
            # It appears that the LastParameter of each point is the same as the FirstParameter
            # of the next, so don't bother duplicating these points.
            points.append(edges[0].valueAt(edges[0].FirstParameter))
            for partEdge in edges:
                points.append(partEdge.valueAt(partEdge.LastParameter))

            simplified_points = PathUtils.simplify3dLine(points)

            # An even number of points is required so that they can be divided into pairs
            # that are then used to create line segments.
            if len(simplified_points) % 2 != 0:
                m = median(simplified_points[-2], simplified_points[-1])
                simplified_points.insert(-1, m)

            simplified_edges = []
            for i in range(0, len(simplified_points) - 1, 2):
                p1 = simplified_points[i]
                p2 = simplified_points[i + 1]
                simplified_edges.append(Part.LineSegment(p1, p2).toShape())

            return simplified_edges

        edges = []
        for e in vWire:
            edges.append(_getPartEdge(e, width_to_depth_calculator))

        return simplify_edges(edges)

    def buildPathMedial(self, obj, width_to_depth_calculator, faces):
        '''constructs a medial axis path using openvoronoi'''

        def insert_many_wires(vd, wires, bitIsLinear):
            for wire in wires:
                PathLog.debug('discretize value: {}'.format(obj.Discretize))
                # Non-linear bits (e.g. ballend mills) need the lines to be split into smaller lengths
                # else they wind up cutting deeper than they should for a given width.
                if bitIsLinear:
                    pts = wire.discretize(QuasiDeflection=obj.Discretize)
                else:
                    pts = wire.discretize(obj.Discretize * 10.0)
                ptv = [FreeCAD.Vector(p.x, p.y) for p in pts]
                ptv.append(ptv[0])

                for i in range(len(pts)):
                    vd.addSegment(ptv[i], ptv[i+1])

        def cutWire(edges):
            path = []
            path.append(Path.Command("G0 Z{}".format(obj.SafeHeight.Value)))
            e = edges[0]
            p = e.valueAt(e.FirstParameter)
            path.append(Path.Command("G0 X{} Y{} Z{}".format(p.x, p.y, obj.SafeHeight.Value)))
            hSpeed = obj.ToolController.HorizFeed.Value
            vSpeed = obj.ToolController.VertFeed.Value
            path.append(Path.Command("G1 X{} Y{} Z{} F{}".format(p.x, p.y, p.z, vSpeed)))
            for e in edges:
                path.extend(PathGeom.cmdsForEdge(e, hSpeed=hSpeed, vSpeed=vSpeed))

            return path

        VD.clear()
        voronoiWires = []
        for f in faces:
            vd = Path.Voronoi()
            insert_many_wires(vd, f.Wires, width_to_depth_calculator.IsLinear)

            vd.construct()

            for e in vd.Edges:
                e.Color = PRIMARY if e.isPrimary() else SECONDARY
            vd.colorExterior(EXTERIOR1)
            vd.colorExterior(EXTERIOR2,
                lambda v: not f.isInside(v.toPoint(f.BoundBox.ZMin),
                obj.Tolerance, True))
            vd.colorColinear(COLINEAR, obj.Colinear)
            vd.colorTwins(TWIN)

            wires = _collectVoronoiWires(vd)
            if _sorting != 'global':
                wires = _sortVoronoiWires(wires)
            voronoiWires.extend(wires)
            VD.append((f, vd, wires))

        if _sorting == 'global':
            voronoiWires = _sortVoronoiWires(voronoiWires)

        pathlist = []
        pathlist.append(Path.Command("(starting)"))
        for w in voronoiWires:
            pWire = self._getPartEdges(obj, w, width_to_depth_calculator)
            if pWire:
                wires.append(pWire)
                pathlist.extend(cutWire(pWire))
        self.commandlist = pathlist

    def opExecute(self, obj):
        '''opExecute(obj) ... process engraving operation'''
        PathLog.track()

        tool = obj.ToolController.Tool
        width_to_depth_calculator = PathWidthToDepth.WidthToDepthCalculator(
                tool, tool.BitShape, self.model[0].Shape.BoundBox.ZMax, obj.FinalDepth.Value)

        if not width_to_depth_calculator.SupportsVcarve:
            FreeCAD.Console.PrintError(
                translate("Path_Vcarve",
                    "This Engraver doesn't support Vcarve.") + "\n")
            return

        try:
            faces = []

            for base in obj.BaseShapes:
                faces.extend(base.Shape.Faces)

            for base in obj.Base:
                for sub in base[1]:
                    shape = getattr(base[0].Shape, sub)
                    if isinstance(shape, Part.Face):
                        faces.append(shape)

            if not faces:
                for model in self.model:
                    if model.isDerivedFrom('Sketcher::SketchObject') or model.isDerivedFrom('Part::Part2DObject'):
                        faces.extend(model.Shape.Faces)

            if faces:
                self.buildPathMedial(obj, width_to_depth_calculator, faces)
            else:
                PathLog.error(translate('PathVcarve', 'The Job Base Object has no engraveable element. Engraving operation will produce no output.'))

        except Exception as e:
            #PathLog.error(e)
            #traceback.print_exc()
            PathLog.error(translate('PathVcarve', 'Error processing Base object. Engraving operation will produce no output.'))
            #raise e

    def opUpdateDepths(self, obj, ignoreErrors=False):
        '''updateDepths(obj) ... engraving is always done at the top most z-value'''
        job = PathUtils.findParentJob(obj)
        self.opSetDefaultValues(obj, job)

    def opSetDefaultValues(self, obj, job):
        '''opSetDefaultValues(obj) ... set depths for vcarving'''
        if PathOp.FeatureDepths & self.opFeatures(obj):
            if job and len(job.Model.Group) > 0:
                bb = job.Proxy.modelBoundBox(job)
                obj.OpStartDepth = bb.ZMax
                obj.OpFinalDepth = job.Stock.Shape.BoundBox.ZMin
            else:
                obj.OpFinalDepth = -0.1

    def isToolSupported(self, obj, tool):
        '''isToolSupported(obj, tool) ... returns True if v-carve op can work with tool.'''

        width_to_depth_calculator = \
            PathWidthToDepth.WidthToDepthCalculator(tool, tool.BitShape)
        return width_to_depth_calculator.SupportsVcarve


def SetupProperties():
    return ["Discretize"]


def Create(name, obj=None):
    '''Create(name) ... Creates and returns a Vcarve operation.'''
    if obj is None:
        obj = FreeCAD.ActiveDocument.addObject("Path::FeaturePython", name)
    ObjectVcarve(obj, name)
    return obj
