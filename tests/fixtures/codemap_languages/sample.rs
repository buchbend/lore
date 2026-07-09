struct Point {
    x: i32,
    y: i32,
}

trait Shape {
    fn area(&self) -> i32;
}

fn make_point() -> Point {
    Point { x: 0, y: 0 }
}
