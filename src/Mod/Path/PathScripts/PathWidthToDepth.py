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
import math

class WidthToDepthCalculator(object):
    def WidthToDepth(self, width):
        raise NotImplementedError

    @property
    def IsLinear(self):
        return NotImplementedError

    @property
    def SupportsVcarve(self):
        return NotImplementedError


class ConicalWidthToDepthCalculator(WidthToDepthCalculator):
    def __init__(self, Diameter, TipDiameter, CuttingEdgeAngle):
        self.rMax = float(Diameter) / 2.0
        self.rMin = float(TipDiameter) / 2.0
        toolangle = math.tan(math.radians(CuttingEdgeAngle / 2.0))
        self.scale = 1.0 / toolangle
        self.zOff = self.rMin * self.scale
        self.SetLimits(0, 0)

    def SetLimits(self, zStart, zFinal):
        self.start = zStart + self.zOff
        zStop  = zStart - self.rMax * self.scale
        self.stop = max(zStop + self.zOff, zFinal)

    def WidthToDepth(self, width):
        depth = width * self.scale
        return max(self.start - depth, self.stop)

    @property
    def IsLinear(self):
        return True

    @property
    def SupportsVcarve(self):
        return True

# To be able to use the spherical calculator the code that uses it has to deal with
# the fact that the depth of cut doesn't change linearly from start depth to end depth.
# This means that each wire to cut must be split into small lengths rather than simply
# a single length with a single start and end point.
class SphericalWidthToDepthCalculator(WidthToDepthCalculator):
    def __init__(self, Diameter):
        self.rMax = float(Diameter) / 2.0
        self.r_squared = self.rMax ** 2
        self.SetLimits(0, 0)

    def SetLimits(self, zStart, zFinal):
        self.start = zStart
        zStop = zStart - self.rMax
        self.stop = max(zStop, zFinal)

    def WidthToDepth(self, width):
        if width < self.rMax:
            depth = self.rMax - math.sqrt(self.r_squared - (width ** 2))
        else:
            depth = self.rMax

        return max(self.start - depth, self.stop)

    @property
    def IsLinear(self):
        return False

    @property
    def SupportsVcarve(self):
        return True

class UnsupportedBitToDepthCalculator(WidthToDepthCalculator):
    def WidthToDepth(self, width):
        return 0

    def SetLimits(self, zStart, zFinal):
        pass

    @property
    def IsLinear(self):
        return True

    @property
    def SupportsVcarve(self):
        return False

def WidthToDepthCalculator(tool, bitShape, zStart=0, zFinal=0):
    # Fixme: Need a better way to determine the shape of a bit.
    if bitShape.endswith("v-bit.fcstd"):
        if tool.CuttingEdgeAngle >= 180.0:
            FreeCAD.Console.PrintError(
                translate("Path_Vcarve",
                    "Engraver Cutting Edge Angle must be < 180 degrees.") + "\n")
            calculator = UnsupportedBitToDepthCalculator()
        else:
            calculator = ConicalWidthToDepthCalculator(
                tool.Diameter, tool.TipDiameter, tool.CuttingEdgeAngle.Value)
    elif bitShape.endswith("ballend.fcstd"):
        calculator = SphericalWidthToDepthCalculator(tool.Diameter)
    else:
        calculator = UnsupportedBitToDepthCalculator()

    calculator.SetLimits(zStart, zFinal)

    return calculator
