interface Shape {
  area(): number;
}

function makeShape(): Shape {
  return new Circle();
}

class Circle implements Shape {
  area() {
    return 1;
  }
}
